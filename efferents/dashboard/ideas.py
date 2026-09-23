"""Idea-scoped, read-only evaluation suites. Never borrow another idea's results."""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import sqlite3

import yaml

from efferents import lab as lab_mod, metrics_view
from efferents.dashboard import reader


def _campaigns(root: Path, lab_id: str) -> list[dict]:
    db = root / 'runs.sqlite'
    if not db.is_file():
        return []
    with sqlite3.connect(f'{db.as_uri()}?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in conn.execute('SELECT * FROM campaigns WHERE lab_id = ?', (lab_id,))]
        except sqlite3.OperationalError:
            return []


def idea_rows(root: Path, cfg, student_id: str) -> list[dict]:
    """Filter attribution before limiting; ambiguous/mislabelled runs are excluded."""
    campaigns = {c['id']: c.get('student_id') or cfg.default_student_id for c in _campaigns(root, cfg.lab_id)}
    db = root / 'runs.sqlite'
    if not db.is_file():
        return []
    with sqlite3.connect(f'{db.as_uri()}?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='runs'").fetchone():
            return []
        result = []
        for row in conn.execute('SELECT * FROM runs ORDER BY started_at DESC'):
            item = dict(row)
            if item.get('status', 'succeeded') != 'succeeded':
                continue
            direct = item.get('student_id')
            campaign = campaigns.get(item.get('campaign_id'))
            if direct and campaign and direct != campaign:
                continue
            # Unknown campaign attribution is not silently assigned to primary.
            if not direct and item.get('campaign_id') and campaign is None:
                continue
            if (direct or campaign or cfg.default_student_id) == student_id:
                result.append(item)
                if len(result) == 120:
                    break
        return result


def _safe_text(path: Path, submission: Path) -> str:
    resolved = path.resolve()
    if resolved.is_relative_to(submission.resolve()) and resolved.is_file():
        return resolved.read_text()[:24000]
    return ''


def read_idea(root: Path, cfg, student_id: str) -> dict:
    root = Path(root).resolve()
    student = next((s for s in cfg.students if s['id'] == student_id), None)
    if student is None:
        raise KeyError(student_id)
    campaigns = [c for c in _campaigns(root, cfg.lab_id)
                 if (c.get('student_id') or cfg.default_student_id) == student_id]
    campaigns.sort(key=lambda c: c.get('opened_at') or '', reverse=True)
    latest = campaigns[0] if campaigns else {}
    hypothesis = _safe_text(Path(latest.get('hypothesis_path') or '/nonexistent'), root.parent)
    if not hypothesis and student_id == cfg.default_student_id:
        hypothesis = _safe_text(root.parent / 'hypothesis.md', root.parent)
    if not hypothesis:
        hypothesis = _safe_text(root.parent / 'popper-corpus' / student_id / 'hypothesis.md', root.parent)
    claim = reader._section(hypothesis, 'Operational restatement') or reader._section(hypothesis, 'Claim')
    falsifier = (reader._section(hypothesis, 'Falsifier(s)')
                 or reader._section(hypothesis, 'Falsifier')
                 or reader._section(hypothesis, 'Stop condition'))
    name = reader._idea_name(student, cfg, latest.get('question', ''))
    suite_path = root.parent / 'ideas' / student_id / 'eval-suite.json'
    status, message, spec = 'configured', '', {}
    scoped_cfg = cfg
    if suite_path.is_file():
        try:
            spec = json.loads(suite_path.read_text())
            if not isinstance(spec, dict) or spec.get('version') != 1:
                raise ValueError('Expected version 1 eval suite')
            raw = yaml.safe_load((root.parent / 'lab.yaml').read_text())
            for key in ('metrics', 'falsifiers', 'evidence'):
                raw[key] = spec.get(key, {} if key != 'falsifiers' else [])
            scoped_cfg = lab_mod._build_labconfig({'slug': student_id}, raw, root.parent, check_paths=False)
            message = str(spec.get('implementation_note') or '')
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            status, message = 'invalid', f'Eval suite needs correction: {exc}'
    elif student_id == cfg.default_student_id:
        # Explicit legacy migration boundary: a lab-wide suite belongs only to
        # its original/default idea, never to newly added students.
        legacy = root.parent / 'eval-suite.json'
        if legacy.is_file():
            try:
                spec = json.loads(legacy.read_text())
                if not isinstance(spec, dict):
                    raise ValueError('Expected an eval suite object')
            except (ValueError, TypeError):
                status, message = 'invalid', 'The original eval suite could not be read.'
    else:
        status, message = 'missing', 'Configure this idea’s eval suite before evaluating it.'
    if status != 'configured':
        scoped_cfg = replace(cfg, metrics=lab_mod.Metrics(
            headline=lab_mod.Headline(latest.get('headline_metric') or 'unconfigured', latest.get('headline_direction') or 'max'),
            panels=()), falsifiers=(), evidence=lab_mod.Evidence())
    # A generated presentation may change without changing the idea's metric
    # contract. Resolve it afresh so regenerated plots appear on the next sync.
    presentation_source = spec.get('presentation_source')
    if presentation_source and status == 'configured':
        try:
            if not isinstance(presentation_source, str):
                raise ValueError('Presentation source must be a relative path')
            path = (root.parent / presentation_source).resolve()
            if Path(presentation_source).is_absolute() or not path.is_relative_to(root.parent):
                raise ValueError('Presentation source must remain inside the lab')
            presentation = json.loads(path.read_text())
            if not isinstance(presentation, dict) or presentation.get('version') != 1:
                raise ValueError('Expected version 1 generated presentation')
            spec = {**spec, **{key: presentation[key] for key in ('title', 'rationale', 'graphs', 'samples') if key in presentation}}
        except (OSError, ValueError, TypeError) as exc:
            status, message = 'invalid', f'Generated eval presentation needs correction: {exc}'
    rows = idea_rows(root, cfg, student_id) if status == 'configured' else []
    evidence, catalog = reader._evidence_payload(root, scoped_cfg, rows=rows)
    # Artifact tokens remain lab-scoped but the displayed records are strictly idea-scoped.
    for record in evidence['records']:
        for artifact in record['artifacts']:
            artifact['url'] = f'/api/labs/{cfg.lab_id}/artifacts/{artifact["token"]}'
    graphs = spec.get('graphs') or [{'title': p.label or p.column, 'columns': [p.column]} for p in scoped_cfg.metrics.panels]
    if not graphs:
        graphs = [{'title': scoped_cfg.metrics.headline.column, 'columns': [scoped_cfg.metrics.headline.column]}]
    allowed = {scoped_cfg.metrics.headline.column, *(p.column for p in scoped_cfg.metrics.panels)}
    views = []
    if not isinstance(graphs, list):
        status, message, graphs = 'invalid', 'Eval graphs must be a list.', []
    for graph in graphs[:12]:
        if not isinstance(graph, dict):
            status, message = 'invalid', 'Invalid eval graph definition.'
            continue
        cols = graph.get('columns', [])
        if not isinstance(cols, list) or not cols or any(not isinstance(col, str) or col not in allowed for col in cols):
            status, message = 'invalid', 'Eval graph references metrics outside this idea’s contract.'
            continue
        eligible = [r for r in reversed(rows) if not metrics_view.constraint_failures(r, cfg=scoped_cfg)]
        views.append({'title': graph.get('title', ''), 'run_ids': [r['run_id'] for r in eligible],
                      'series': [{'column': col, 'points': [{'run_id': r['run_id'], 'value': metrics_view.finite(r.get(col))}
                                  for r in eligible if metrics_view.finite(r.get(col)) is not None]} for col in cols]})
    return {'student_id': student_id, 'name': name, 'focus': student.get('focus') or claim,
            'hypothesis': {'question': latest.get('question') or claim or student.get('focus', ''),
                           'claim': claim, 'falsifier': falsifier, 'student': student_id},
            'campaign_ids': [c['id'] for c in campaigns],
            'suite': {'status': status, 'title': spec.get('title') or f'{name} · eval suite',
                      'rationale': spec.get('rationale', ''), 'message': message,
                      'graphs': views, 'samples': spec.get('samples', []), 'source': str(suite_path.relative_to(root.parent)) if suite_path.exists() else 'default idea contract'},
            'runs': reader.read_runs(root, cfg=scoped_cfg, rows=rows),
            'evidence': evidence, 'verdict': reader.read_verdict(root, scoped_cfg, rows=rows)}
