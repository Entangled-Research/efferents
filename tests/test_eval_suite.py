import json
from pathlib import Path

from types import SimpleNamespace

import pytest

from efferents.eval_suite import validate_suite, view
from efferents.lab import LabConfig

FIXTURE = Path(__file__).parent / "fixtures/sample_submission"


def config():
    return LabConfig.from_submission(FIXTURE)


def suite(cfg):
    return {'version': 1, 'title': 'Measured evaluation', 'rationale': 'Compare actual outputs.',
            'graphs': [{'title': 'Score', 'columns': [cfg.metrics.headline.column]}],
            'samples': [{'kind': 'prediction_samples', 'description': 'Held-out predictions'}]}


def test_unknown_metric_and_no_headline_rejected():
    cfg = config()
    raw = suite(cfg)
    raw['graphs'][0]['columns'] = ['invented_accuracy']
    with pytest.raises(ValueError, match='undeclared'):
        validate_suite(raw, cfg)


def test_read_only_measured_graphs_and_missing_suite(tmp_path):
    cfg = config()
    root = tmp_path / 'lab'
    root.mkdir()
    assert view(root, cfg, [])['status'] == 'missing'
    (tmp_path / 'eval-suite.json').write_text(json.dumps(suite(cfg)))
    result = view(root, cfg, [{'run_id': 'measured', cfg.metrics.headline.column: .4}])
    assert result['status'] == 'configured'
    # Constraint failures can exclude a measurement but can never invent one.
    points = result['graphs'][0]['series'][0]['points']
    assert not points or points == [{'run_id': 'measured', 'value': .4}]
    assert view(root, cfg, [])['graphs'][0]['series'][0]['points'] == []


def test_generation_budget_and_idempotence(tmp_path, monkeypatch):
    import shutil
    from efferents.eval_suite import generate
    from efferents.agents import model_client, budget
    sub = tmp_path / 'submission'
    shutil.copytree(FIXTURE, sub)
    cfg = LabConfig.from_submission(sub)
    calls = []
    response = SimpleNamespace(content=[SimpleNamespace(type='text', text=json.dumps(suite(cfg)))],
                               usage=SimpleNamespace(input_tokens=120, output_tokens=60))
    def make_client(budget):
        assert budget.daily_cap == cfg.budget.daily_cap_usd
        calls.append(budget)
        return SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: response))
    monkeypatch.setattr(model_client, 'make_client', make_client)
    monkeypatch.setattr(budget, 'model_for', lambda role: 'claude-sonnet-4-6')
    generate(sub)
    generate(sub)
    assert len(calls) == 1
    assert json.loads((sub / 'lab/budget.jsonl').read_text())['agent'] == 'eval_suite'
    assert len(list((sub / 'context/eval-suites').glob('*.json'))) == 1
