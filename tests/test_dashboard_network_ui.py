from pathlib import Path

from efferents.dashboard.report_theme import REPORT_CSS


STATIC = Path(__file__).resolve().parents[1] / "efferents" / "dashboard" / "static"
PROGRESS = STATIC.parents[1] / "agents" / "progress.py"


def test_dashboard_has_portfolio_rail_and_network_map():
    html = (STATIC / "dashboard.html").read_text()
    javascript = (STATIC / "dashboard.js").read_text()

    assert 'id="lab-rail"' in html
    assert 'id="lab-list"' in html
    assert 'data-route-view="network"' in html
    assert 'id="lab-map"' in html
    # No public/private choice is surfaced; publication stays out of UI scope.
    assert "data-network-scope" not in html
    assert "Private until authorized" not in html
    assert "private by default" not in html
    assert 'getJSON("/api/labs")' in javascript
    # Selection is per browser: the observer fetches lab-scoped routes rather
    # than switching a server-wide selected lab.
    assert 'postJSON("/api/labs/select"' not in javascript
    assert "function labPath(kind)" in javascript
    assert 'labPath("control")' in javascript
    assert "renderNetwork();" in javascript


def test_shared_visual_contract_is_minimal_paper_and_ink_research_ledger():
    css = (STATIC / "dashboard.css").read_text()
    html = (STATIC / "dashboard.html").read_text()
    progress = PROGRESS.read_text()

    assert "--bg: #f1ede2;" in css
    assert "--panel: #faf8f0;" in css
    assert "--panel-raised: #f3efe3;" in css
    assert "--signal: #2d5379;" in css
    assert "--terracotta: #a8502b;" in css
    assert "--mustard: #a8842d;" in css
    assert "--orange: #c05a2e;" in css
    assert "#03befc" not in css
    assert "#0057ff" not in css
    assert "--shadow: none;" in css
    assert "--display:" in css
    assert "--sans: -apple-system" in css
    assert html.count(">ℯ</span>") == 2
    assert ">EF</span>" not in html
    assert "color-mix" not in css
    assert "backdrop-filter" not in css
    assert "#f7fbff" not in css
    assert "#eef7fd" not in css
    assert "#356f50" not in css
    assert ".lab-list-item.selected" in css
    assert "overflow-wrap: anywhere;" in css
    assert "grid-template-columns: minmax(110px, .8fr) minmax(150px, 1.2fr);" in css
    assert ".lab-map" in css
    assert "color-scheme: light;" in REPORT_CSS
    assert "--signal: #003b80;" in REPORT_CSS
    assert "--mustard: #d4a017;" in REPORT_CSS
    assert "--orange: #f06c00;" in REPORT_CSS
    assert "#03befc" not in REPORT_CSS
    assert "--sans: var(--display);" in REPORT_CSS
    assert 'content: "efferents / research record";' in REPORT_CSS
    assert 'content: "EF / RESEARCH RECORD";' not in REPORT_CSS
    assert "linear-gradient" not in REPORT_CSS
    assert "color-scheme: light;" in progress
    assert "#69ddd0" not in progress
    assert "#b9f36a" not in progress
    assert "--bg: #ffffff;" in progress
    assert "--signal: #003b80;" in progress
    assert "--mustard: #d4a017;" in progress
    assert "--orange: #f06c00;" in progress
    assert "#03befc" not in progress
    assert "--sans: var(--display);" in progress
    assert "#f7fbff" not in progress


def test_observer_is_compact_validity_aware_and_supports_visual_evidence():
    html = (STATIC / "dashboard.html").read_text()
    css = (STATIC / "dashboard.css").read_text()
    javascript = (STATIC / "dashboard.js").read_text()

    assert 'id="metric-eligible"' in html
    assert 'id="metric-median"' in html
    assert 'id="metric-iqr"' in html
    assert 'id="evidence-panel"' in html
    assert 'preserveAspectRatio="xMidYMid meet"' in html
    assert "font: 550 clamp(27px, 3vw, 42px)/1 var(--mono);" in css
    assert ".evidence-gallery" in css
    assert ".evidence-comparison-grid" in css
    assert "run.eligible !== false" in javascript
    assert '"/api/evidence"' in javascript
    assert "groupEvidenceRecords" in javascript
    assert "Matched comparison" in javascript
    assert "Eligible-run summary statistics" in html
