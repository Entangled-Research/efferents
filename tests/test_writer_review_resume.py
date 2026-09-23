"""Interrupted publication retries missing work against the exact saved draft."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

import pytest
import yaml

from efferents import lab
from efferents.agents import reviewer, writer
from efferents.agents.federation import parse_journal_entries


@pytest.mark.parametrize('failure_phase', ['review', 'rebuttal', 'changed_review', 'journal'])
def test_retry_keeps_manuscript_and_successful_reviews_without_double_publication(
    tmp_path, monkeypatch, fake_anthropic_factory, failure_phase
):
    sub = tmp_path / 'submission'
    shutil.copytree(Path(__file__).parent / 'fixtures/sample_submission', sub)
    raw = yaml.safe_load((sub / 'lab.yaml').read_text())
    raw['metrics'] = {'headline': {'column': 'candidate', 'direction': 'min',
                                  'comparator_column': 'prior', 'aggregate': 'mean'}}
    raw['falsifiers'] = []
    (sub / 'lab.yaml').write_text(yaml.safe_dump(raw))
    cfg = lab.LabConfig.from_submission(sub)
    monkeypatch.setattr(lab, '_active', cfg)
    monkeypatch.setattr(lab, 'PEER_REVIEW_ENABLED', True)
    monkeypatch.setattr(lab, 'PEER_REVIEW_ACCEPT_MEAN_THRESHOLD', 6.0)
    monkeypatch.setattr(lab, 'PEER_REVIEW_ACCEPT_MIN_THRESHOLD', 4)
    root = sub / 'lab'
    root.mkdir(exist_ok=True)
    paths = writer.writer_paths(lab=root, paper=sub/'paper', reports=root/'reports', context=sub/'context')
    with sqlite3.connect(paths.runs_db) as conn:
        conn.execute('CREATE TABLE runs (run_id TEXT, campaign_id TEXT, started_at TEXT, status TEXT, seed INTEGER, candidate REAL, prior REAL)')
        conn.execute('CREATE TABLE campaigns (id TEXT, closed_at TEXT, close_reason TEXT)')
        conn.execute('INSERT INTO campaigns VALUES (?,NULL,NULL)', ('c1',))
        for i in range(3):
            conn.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?)', (f'r{i}', 'c1', str(i), 'succeeded', i, .4, .5))
        before = conn.execute('SELECT * FROM runs ORDER BY run_id').fetchall()
    hypothesis = (sub/'hypothesis.md').read_text()
    campaign = {'id': 'c1', 'question': 'Bounded measured comparison',
                'hypothesis_path': 'hypothesis.md', 'hypothesis_hash': 'sha256:'+hashlib.sha256(hypothesis.encode()).hexdigest()}
    body = '\n'.join(f'## {s}\n\nActual measured comparison.\n' for s in ('Motivation','Methods','Results','Conclusion','Next questions'))
    client = fake_anthropic_factory([body])
    reviews_called = Counter()
    rebuttals = []
    reviewed_hashes = []

    def review(**kwargs):
        persona = kwargs['persona']
        reviews_called[persona] += 1
        reviewed_hashes.append(hashlib.sha256(kwargs['paper_path'].read_bytes()).hexdigest())
        if failure_phase in {'review', 'changed_review'} and persona == 'neutral' and reviews_called[persona] == 1:
            raise RuntimeError('temporary provider failure')
        result = reviewer.Review(persona, 7, 'Bounded claim supported', confidence=4)
        if hasattr(result, 'material_flaw'):
            result.material_flaw = False
        return result

    def rebuttal(**kwargs):
        rebuttals.append(kwargs['paper_path'].read_bytes())
        if failure_phase == 'rebuttal' and len(rebuttals) == 1:
            raise RuntimeError('temporary rebuttal failure')
        return '## Rebuttal\n\nThe measured scope is retained.'

    monkeypatch.setattr(reviewer, 'review', review)
    monkeypatch.setattr('efferents.agents.rebuttal.write_rebuttal', rebuttal)
    monkeypatch.setattr('efferents.agents.journal.auto_commit_paper', lambda **kwargs: 'commit')
    if failure_phase == 'journal':
        from efferents.agents import journal
        original_append = journal.append_journal

        def interrupted_after_append(*args, **kwargs):
            original_append(*args, **kwargs)
            raise RuntimeError('process interrupted after journal append')

        monkeypatch.setattr(journal, 'append_journal', interrupted_after_append)
        with pytest.raises(RuntimeError, match='interrupted after journal'):
            writer.write_phase_a_paper(paths, campaign, client)
        first = (paths.paper/'c1.md').read_bytes().decode('utf-8')
    else:
        first = writer.write_phase_a_paper(paths, campaign, client)
    draft = (paths.paper/'c1.md').read_bytes()
    digest = hashlib.sha256(draft).hexdigest()
    state = json.loads((paths.lab/'peer_review/c1'/f'{digest}.json').read_text())
    assert len(state['reviews']) == (2 if failure_phase in {'review', 'changed_review'} else 3)
    assert not writer.review_complete(paths, 'c1')
    assert (paths.paper/'journal.md').exists() == (failure_phase == 'journal')
    with sqlite3.connect(paths.runs_db) as conn:
        assert conn.execute('SELECT closed_at FROM campaigns').fetchone()[0] is None
    if failure_phase == 'changed_review':
        # An explicit draft revision must receive all three new reviews.
        draft += b'\n<!-- Owner clarification before decision -->\n'
        draft = draft.replace(b'\n', b'\r\n')  # exact CRLF bytes must be reviewable
        (paths.paper/'c1.md').write_bytes(draft)
    second = writer.write_phase_a_paper(paths, campaign, client)
    third = writer.write_phase_a_paper(paths, campaign, client)
    assert second == third
    assert first == second if failure_phase != 'changed_review' else first != second
    assert (paths.paper/'c1.md').read_bytes() == draft
    assert len(client.calls) == 1  # composition never repeats
    assert reviews_called == Counter({'critical': 2 if failure_phase == 'changed_review' else 1, 'neutral': 1 if failure_phase in {'rebuttal', 'journal'} else 2, 'optimistic': 2 if failure_phase == 'changed_review' else 1})
    assert len(rebuttals) == (2 if failure_phase == 'rebuttal' else 1)
    assert set(reviewed_hashes) == {digest, hashlib.sha256(draft).hexdigest()}
    assert writer.review_complete(paths, 'c1')
    entries = parse_journal_entries((paths.paper/'journal.md').read_text())
    assert [entry['campaign_id'] for entry in entries] == ['c1']
    with sqlite3.connect(paths.runs_db) as conn:
        assert conn.execute('SELECT * FROM runs ORDER BY run_id').fetchall() == before
        assert conn.execute('SELECT close_reason FROM campaigns').fetchone()[0] == 'published'


def test_competing_writers_atomically_keep_one_complete_draft(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    destination = tmp_path / 'paper.md'
    barrier = Barrier(2)
    bodies = ['first\r\n' + 'A'*100000, 'second\n' + 'Ω'*100000]

    def publish(body):
        barrier.wait()
        return writer._install_draft(destination, body)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(publish, bodies))
    stored = destination.read_bytes().decode('utf-8')
    assert stored in bodies and results == [stored, stored]
    assert not list(tmp_path.glob('.draft-*'))
