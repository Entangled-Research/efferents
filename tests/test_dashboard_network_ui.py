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
    assert 'id="event-admin-panel"' in html
    assert "network-lab-boundary" in javascript
    assert "read only" in javascript
    # The network route renders the animated live graph; the entry page stays
    # focused on onboarding and does not embed the illustrative prototype.
    assert 'src="/prototypes/event-network.html"' not in html
    assert "network-packet" in javascript


def test_network_map_is_a_pan_zoom_viewport():
    html = (STATIC / "dashboard.html").read_text()
    css = (STATIC / "dashboard.css").read_text()
    javascript = (STATIC / "dashboard.js").read_text()

    # The map is a focusable fixed viewport; every layer lives in one world.
    assert 'id="lab-map" class="lab-map living-network" tabindex="0"' in html
    world = html.index('id="network-world"')
    for layer in ("network-lines", "network-journals", "network-nodes", "network-ideas"):
        assert html.index(f'id="{layer}"') > world
    assert 'data-map-zoom="in"' in html
    assert 'data-map-zoom="out"' in html
    assert 'data-map-zoom="fit"' in html
    assert 'id="map-zoom-level"' in html
    assert "transform-origin: 0 0;" in css
    assert ".map-controls" in css
    # Wheel zoom must be able to preventDefault, so the listener is non-passive.
    assert "}, {passive: false});" in javascript
    assert "setPointerCapture" in javascript
    assert "initMapPanZoom();" in javascript
    assert "translate(${mapView.x}px, ${mapView.y}px) scale(${mapView.k})" in javascript
    # Both local and remote labs expose an inspector; local evals open the observer.
    assert 'href="${esc(labHref(lab.lab_id))}"' in javascript
    assert 'Open eval suite' in javascript
    assert "await openLabTab(lab.lab_id)" in javascript
    # Layout follows the viewport shape and the lab set, not the poll interval.
    assert "function chooseMapLayout(sizes)" in javascript
    assert "cards.get(lab.lab_id).offsetHeight" in javascript
    assert "change.view || (change.content && !mapView.moved)" in javascript
    assert 'lines.setAttribute("viewBox", `0 0 ${world.width} ${world.height}`);' in javascript
    # The map no longer grows to its content height.
    assert "map.style.minHeight" not in javascript


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
    assert 'renderEvidence(data.evidence, false)' in javascript
    assert "groupEvidenceRecords" in javascript
    assert "Matched comparison" in javascript
    assert "Eligible-run summary statistics" in html


def test_ideas_are_named_and_carry_the_verdict_not_the_lab():
    static = Path(__file__).resolve().parents[1] / "efferents" / "dashboard" / "static"
    js = (static / "dashboard.js").read_text()
    html = (static / "dashboard.html").read_text()
    # Ideas are listed by name inside their lab, on the map and in the rail.
    assert "function labIdeas(lab)" in js
    assert "esc(ideaName(idea))" in js
    assert 'class="lab-idea-label">Ideas · ${ideas.length}' in js
    assert "Idea ${String.fromCharCode" not in js
    # A lab is never marked falsified; only an idea is.
    assert "lab-verdict" not in js
    assert 'idea.verdict === "falsified"' in js
    assert '<h2 id="verdict-title">Idea verdict</h2>' in html


def test_journal_panel_has_no_explanatory_copy_and_hides_when_empty():
    static = Path(__file__).resolve().parents[1] / "efferents" / "dashboard" / "static"
    html = (static / "dashboard.html").read_text()
    js = (static / "dashboard.js").read_text()
    for removed in ("Labs communicate only", "exchange-count",
                    "exchange-explanation"):
        assert removed not in html and removed not in js
    assert '<section id="exchange-panel" class="panel exchange-panel" ' \
           'aria-label="Published journal papers" hidden>' in html
    assert 'document.getElementById("exchange-panel").hidden = !rows.length;' in js
