from pathlib import Path


STATIC = Path(__file__).resolve().parents[1] / "efferents" / "dashboard" / "static"


def test_dashboard_has_connect_steer_observe_entry_flow():
    html = (STATIC / "dashboard.html").read_text()

    assert 'id="connect-form"' in html
    assert 'id="steer-form"' in html
    assert 'data-route-view="observe"' in html
    assert 'id="runtime-confirm-check"' in html
    assert 'data-intake-tab="agent"' in html
    assert 'data-intake-tab="submit"' in html


def test_paused_demo_copy_and_controls_are_present():
    js = (STATIC / "dashboard.js").read_text()
    assert "Paused demo · no model calls" in js
    # The connection bar shows the lab's real source, never invented demo copy.
    assert "Read-only QML evidence snapshot" not in js
    assert "QML" not in js
    assert 'document.getElementById("start-lab").hidden = pausedDemo' in js


def test_dashboard_is_light_only_paper_ledger():
    css = (STATIC / "dashboard.css").read_text()
    html = (STATIC / "dashboard.html").read_text()
    javascript = (STATIC / "dashboard.js").read_text()

    assert ":root {\n  color-scheme: light;" in css
    # Light-only by design: no dark palette, no theme toggle, no persistence.
    assert ':root[data-theme="dark"]' not in css
    assert "prefers-color-scheme" not in css
    assert 'id="theme-toggle"' not in html
    assert "efferents-theme" not in javascript
    assert '<meta name="color-scheme" content="light">' in html


def test_dashboard_scripts_are_external_for_strict_script_csp():
    html = (STATIC / "dashboard.html").read_text()

    assert '<script src="/static/dashboard.js"></script>' in html
    assert "<script>" not in html


def test_observe_side_panels_stick_until_replaced_and_can_hide():
    html = (STATIC / "dashboard.html").read_text()
    css = (STATIC / "dashboard.css").read_text()
    javascript = (STATIC / "dashboard.js").read_text()

    assert html.count("data-panel-toggle") == 2
    assert ".side-stack .panel {\n  position: sticky;" in css
    assert ".panel.collapsed .records-list" in css
    assert "initPanelToggles();" in javascript
    assert "initTheme();" not in javascript
