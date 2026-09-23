import json
from dataclasses import replace
from pathlib import Path
import shutil
import sqlite3

import pytest
import yaml

from efferents.dashboard.ideas import read_idea, idea_rows
from efferents.lab import LabConfig


@pytest.fixture
def two_ideas(tmp_path):
    source = Path(__file__).resolve().parents[1] / 'examples/smoke-lab'
    root = tmp_path / 'submission'
    shutil.copytree(source, root)
    lab = root / 'lab'
    lab.mkdir(exist_ok=True)
    cfg = LabConfig.from_submission(root)
    cfg = replace(cfg, students=({'id': 'primary', 'focus': 'Original claim'}, {'id': 'second', 'focus': 'New claim'}))
    with sqlite3.connect(lab / 'runs.sqlite') as c:
        c.execute('CREATE TABLE campaigns (id TEXT, lab_id TEXT, student_id TEXT, question TEXT, hypothesis_path TEXT, opened_at TEXT)')
        c.execute('INSERT INTO campaigns VALUES (?, ?, ?, ?, ?, ?)', ('c1', cfg.lab_id, 'primary', 'Original', '', '2026-01-01'))
        c.execute('INSERT INTO campaigns VALUES (?, ?, ?, ?, ?, ?)', ('c2', cfg.lab_id, 'second', 'New', '', '2026-01-02'))
        c.execute('CREATE TABLE runs (run_id TEXT, started_at TEXT, student_id TEXT, campaign_id TEXT, status TEXT, synthetic_loss REAL, second_score REAL)')
        c.executemany('INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?)', [
            ('old', '1', 'primary', 'c1', 'succeeded', .1, None),
            ('new', '2', 'second', 'c2', 'succeeded', 999, .8),
            ('conflict', '3', 'primary', 'c2', 'succeeded', 999, 999),
            ('failed', '4', 'second', 'c2', 'failed', 999, 999),
        ])
    return lab, cfg


def test_new_idea_never_inherits_default_suite_or_results(two_ideas):
    root, cfg = two_ideas
    primary = read_idea(root, cfg, 'primary')
    second = read_idea(root, cfg, 'second')
    assert [r['run_id'] for r in primary['runs']['runs']] == ['old']
    assert second['suite']['status'] == 'missing'
    assert second['runs']['runs'] == []
    assert second['verdict']['verdict'] == 'undecided'
    assert second['hypothesis']['question'] == 'New'
    assert primary['hypothesis']['question'] == 'Original'


def test_distinct_contract_and_scope_cover_best_graphs_verdict(two_ideas):
    root, cfg = two_ideas
    path = root.parent / 'ideas/second/eval-suite.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'version': 1, 'title': 'Second only',
        'metrics': {'headline': {'column': 'second_score', 'direction': 'max'},
                    'panels': [{'column': 'second_score', 'label': 'Second score', 'direction': 'max'}]},
        'falsifiers': [{'id': 'too-low', 'description': 'Below target', 'when': {'column': 'second_score', 'agg': 'mean', 'op': '<=', 'value': .7, 'min_n': 1}}],
        'graphs': [{'title': 'Second score', 'columns': ['second_score']}]}))
    view = read_idea(root, cfg, 'second')
    assert view['suite']['status'] == 'configured'
    assert view['runs']['history'] == {'total': 1, 'best': .8, 'best_run_id': 'new'}
    assert view['runs']['headline']['column'] == 'second_score'
    assert view['suite']['graphs'][0]['series'][0]['points'] == [{'run_id': 'new', 'value': .8}]
    assert view['verdict']['n_runs'] == 1
    assert view['verdict']['falsifiers'][0]['id'] == 'too-low'


def test_unknown_idea_and_conflicting_attribution_are_rejected(two_ideas):
    root, cfg = two_ideas
    with pytest.raises(KeyError):
        read_idea(root, cfg, '../primary')
    assert [r['run_id'] for r in idea_rows(root, cfg, 'second')] == ['new']


def test_filtering_precedes_limit(two_ideas):
    root, cfg = two_ideas
    with sqlite3.connect(root/'runs.sqlite') as c:
        c.executemany('INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?)',
                      [(f'n{i}', f'9{i:04}', 'second', 'c2', 'succeeded', None, .5) for i in range(150)])
    assert [r['run_id'] for r in idea_rows(root, cfg, 'primary')] == ['old']


def test_suite_is_separate_from_lab_contract(two_ideas):
    root, cfg = two_ideas
    before = yaml.safe_load((root.parent/'lab.yaml').read_text())
    read_idea(root, cfg, 'second')
    assert yaml.safe_load((root.parent/'lab.yaml').read_text()) == before
