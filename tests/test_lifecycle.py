from efferents.lifecycle import inactive, remove, state


def test_deletion_retains_evidence_and_is_idempotent(tmp_path):
    evidence = tmp_path / 'runs.sqlite'
    evidence.write_bytes(b'preserved evidence')
    remove(tmp_path, by='owner', student_id='primary')
    first = state(tmp_path)
    remove(tmp_path, by='retry', student_id='primary')
    assert state(tmp_path) == first
    assert inactive(tmp_path, 'primary')
    assert not inactive(tmp_path, 'other')
    remove(tmp_path, by='owner')
    assert inactive(tmp_path, 'other')
    assert evidence.read_bytes() == b'preserved evidence'


def test_deleted_idea_cannot_execute(tmp_path, monkeypatch):
    from efferents.agents.executor import execute
    from types import SimpleNamespace
    remove(tmp_path, by='owner', student_id='primary')
    result = execute(paths=SimpleNamespace(root=tmp_path), proposal={'student_id': 'primary'})
    assert result['blocked'] and not result['ok']
