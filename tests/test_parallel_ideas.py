from concurrent.futures import ThreadPoolExecutor
import threading
from dataclasses import replace

from efferents import lab
from efferents.agents import orchestrator, executor
from efferents.agents.state import (
    lab_paths, init_lab, queue_push, queue_pop_batch, queue_ack,
    append_jsonl, read_jsonl,
)
from efferents.agents.workspaces import prepare_run_workspace


def test_batch_claim_rotation_and_restart(tmp_path):
    paths = lab_paths(tmp_path / 'lab')
    init_lab(paths)
    for idea in ['a', 'a', 'b', 'b', 'c']:
        queue_push(paths.queue, {'student_id': idea})
    assert [p['student_id'] for p in queue_pop_batch(paths.queue, 2)] == ['a', 'b']
    assert [p['student_id'] for p in read_jsonl(paths.queue)] == ['c', 'a', 'b']
    init_lab(paths)
    assert [p['student_id'] for p in read_jsonl(paths.queue)] == ['a', 'b', 'c', 'a', 'b']
    assert not paths.inflight.exists()
    queue_pop_batch(paths.queue, 2)
    queue_ack(paths.queue)
    assert not paths.inflight.exists()


def test_workspaces_do_not_share_outputs_or_recurse(tmp_path):
    source = tmp_path / 'submission'
    source.mkdir()
    (source / 'experiment.py').write_text('original')
    (source / '.env').write_text('private')
    root = source / 'lab'
    root.mkdir()
    one = prepare_run_workspace(root, source, {'student_id': '../../a'}, 'run1')
    two = prepare_run_workspace(root, source, {'student_id': 'b'}, 'run2')
    (one / 'experiment.py').write_text('changed')
    assert (two / 'experiment.py').read_text() == 'original'
    assert (source / 'experiment.py').read_text() == 'original'
    assert not (one / 'lab').exists()
    assert not (one / '.env').exists()


def test_concurrent_append_preserves_long_records(tmp_path):
    path = tmp_path / 'ledger.jsonl'
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: append_jsonl(path, {'i': i, 'data': 'x' * 20000}), range(50)))
    assert sorted(p['i'] for p in read_jsonl(path)) == list(range(50))


def test_step_executes_distinct_ideas_concurrently(tmp_path, monkeypatch):
    monkeypatch.setattr(lab, "student_ids", lambda: ["a", "b", "c"])
    orch = orchestrator.Orchestrator(lab_dir=tmp_path / 'lab', context_dir=tmp_path / 'context', dry_run=True, max_parallel_ideas=2)
    for sid in ['a', 'b', 'c']:
        queue_push(orch.paths.queue, {'student_id': sid, 'name': sid})
    monkeypatch.setattr(orch, '_refill_queue', lambda: 0)
    for name in ['_maybe_digest', '_maybe_code', '_maybe_write']:
        monkeypatch.setattr(orch, name, lambda: None)
    from efferents import event
    from efferents.agents import conference
    monkeypatch.setattr(event, 'exchange', lambda *a, **k: None)
    monkeypatch.setattr(conference, 'attend', lambda **k: None)
    gate = threading.Barrier(2)
    seen = []
    def execute(*, paths, proposal, isolated):
        assert isolated
        gate.wait(timeout=5)
        seen.append(proposal['student_id'])
        return {'ok': False, 'name': proposal['name']}
    monkeypatch.setattr(executor, 'execute', execute)
    assert orch.step()['event'] == 'ran'
    assert sorted(seen) == ['a', 'b']
    assert read_jsonl(orch.paths.queue)[0]['student_id'] == 'c'
    assert not orch.paths.inflight.exists()


def test_owner_pause_and_deletion_prevent_subprocess(tmp_path, monkeypatch):
    paths = lab_paths(tmp_path / 'lab')
    init_lab(paths)
    from efferents import lifecycle, steer
    monkeypatch.setattr(executor, '_execute_run', lambda *a, **k: (_ for _ in ()).throw(AssertionError('must not run')))
    monkeypatch.setattr(steer, 'owner_paused', lambda root: 'Owner requested pause')
    assert executor.execute(paths=paths, proposal={'name': 'paused'}, isolated=True)['deferred']
    monkeypatch.setattr(lifecycle, 'inactive', lambda *args: True)
    assert executor.execute(paths=paths, proposal={'name': 'deleted'}, isolated=True)['blocked']


def test_isolated_command_runs_in_snapshot(tmp_path, monkeypatch):
    from efferents.exec import _execute_run
    source = tmp_path / 'source'
    source.mkdir()
    script = source / 'experiment.py'
    script.write_text('import json\nfrom pathlib import Path\nPath("output.txt").write_text("evidence")\nprint(json.dumps({"metrics": {"score": 1}}))\n')
    cfg = lab.get_config()
    monkeypatch.setattr(lab, '_active', replace(cfg, source=replace(cfg.source, dir=source), executor=replace(cfg.executor, run_command=f'python3 {script} {{config_path}}')))
    workspace = prepare_run_workspace(tmp_path / 'lab', source, {'student_id': 'a'}, 'one')
    config = tmp_path / 'config.yaml'
    config.write_text('{}')
    result = _execute_run(config, source_dir=workspace)
    assert result.ok
    assert (workspace / 'output.txt').read_text() == 'evidence'
    assert not (source / 'output.txt').exists()


def test_failed_worker_requeues_only_failed_idea(tmp_path, monkeypatch):
    monkeypatch.setattr(lab, 'student_ids', lambda: ['a', 'b'])
    orch = orchestrator.Orchestrator(lab_dir=tmp_path / 'lab', context_dir=tmp_path / 'context', dry_run=True, max_parallel_ideas=2)
    for sid in ['a', 'b']:
        queue_push(orch.paths.queue, {'student_id': sid, 'name': sid})
    monkeypatch.setattr(orch, '_refill_queue', lambda: 0)
    for name in ['_maybe_digest', '_maybe_code', '_maybe_write']:
        monkeypatch.setattr(orch, name, lambda: None)
    from efferents import event
    from efferents.agents import conference
    monkeypatch.setattr(event, 'exchange', lambda *a, **k: None)
    monkeypatch.setattr(conference, 'attend', lambda **k: None)
    def execute(*, paths, proposal, isolated):
        if proposal['student_id'] == 'a':
            raise RuntimeError('worker failed')
        return {'ok': False, 'name': 'b'}
    monkeypatch.setattr(executor, 'execute', execute)
    orch.step()
    assert [p['student_id'] for p in read_jsonl(orch.paths.queue)] == ['a']


def test_parallel_default_requires_capability_and_multiple_ideas(tmp_path, monkeypatch):
    monkeypatch.delenv('EFFERENTS_MAX_PARALLEL_IDEAS', raising=False)
    cfg = lab.get_config()
    monkeypatch.setattr(lab, 'student_ids', lambda: ['a', 'b'])
    custom = orchestrator.Orchestrator(lab_dir=tmp_path / 'custom', dry_run=True)
    assert custom.max_parallel_ideas == 1
    monkeypatch.setattr(lab, '_active', replace(cfg, executor=replace(cfg.executor, isolated_parallel_safe=True)))
    starter = orchestrator.Orchestrator(lab_dir=tmp_path / 'starter', dry_run=True)
    assert starter.max_parallel_ideas == 10
    monkeypatch.setattr(lab, 'student_ids', lambda: ['a'])
    single = orchestrator.Orchestrator(lab_dir=tmp_path / 'single', dry_run=True)
    assert single.max_parallel_ideas == 1
    monkeypatch.setenv('EFFERENTS_MAX_PARALLEL_IDEAS', '3')
    explicit = orchestrator.Orchestrator(lab_dir=tmp_path / 'explicit', dry_run=True)
    assert explicit.max_parallel_ideas == 3
