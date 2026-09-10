from pathlib import Path

import pytest

from efferents.dashboard.theme import (
    RESEARCH_THEME_CSS,
    embed_research_theme,
)


ROOT = Path(__file__).resolve().parents[1]


def test_theme_contract_is_light_only_paper_ledger():
    assert RESEARCH_THEME_CSS.startswith(":root {\n  color-scheme: light;")
    # Light-only: a single warm-paper palette, no dark variant to maintain.
    assert ':root[data-theme="dark"]' not in RESEARCH_THEME_CSS
    assert "prefers-color-scheme" not in RESEARCH_THEME_CSS
    assert "--bg: #f1ede2;" in RESEARCH_THEME_CSS
    assert "--panel: #faf8f0;" in RESEARCH_THEME_CSS
    # Hairlines and text carry their own neutral hierarchy; azulejo cobalt stays
    # the rare signal accent instead of doubling as every border and paragraph.
    assert "--line: #c9c1ad;" in RESEARCH_THEME_CSS
    assert "--ink: #211d15;" in RESEARCH_THEME_CSS
    assert "--fg: #211d15;" in RESEARCH_THEME_CSS
    assert "--signal: #2d5379;" in RESEARCH_THEME_CSS
    assert "--on-signal: #f7f3e8;" in RESEARCH_THEME_CSS
    assert "--data: #33614d;" in RESEARCH_THEME_CSS
    # The Portuguese trio: cobalt signal, terracotta warmth, cream ground.
    assert "--terracotta: #a8502b;" in RESEARCH_THEME_CSS
    assert "--warning: var(--terracotta);" in RESEARCH_THEME_CSS
    assert "--danger: var(--terracotta);" in RESEARCH_THEME_CSS
    assert "--mustard: #a8842d;" in RESEARCH_THEME_CSS
    assert "--orange: #c05a2e;" in RESEARCH_THEME_CSS
    # Azulejo colors only — no literal tile pattern or fetched imagery.
    assert "--frieze" not in RESEARCH_THEME_CSS
    assert "url(" not in RESEARCH_THEME_CSS
    # Each accent is defined exactly once — no per-mode redefinitions.
    assert RESEARCH_THEME_CSS.count("#2d5379") == 1
    assert RESEARCH_THEME_CSS.count("#a8502b") == 1
    assert RESEARCH_THEME_CSS.count("#a8842d") == 1
    assert RESEARCH_THEME_CSS.count("#c05a2e") == 1
    assert "#03befc" not in RESEARCH_THEME_CSS
    assert "#0057ff" not in RESEARCH_THEME_CSS
    assert "#003b80" not in RESEARCH_THEME_CSS
    assert "--display:" in RESEARCH_THEME_CSS
    # 80s-workstation type: mono-led headings and data, plain sans for prose.
    assert "--display: var(--mono);" in RESEARCH_THEME_CSS
    assert "font-style: italic" not in RESEARCH_THEME_CSS
    assert "--sans: var(--display);" not in RESEARCH_THEME_CSS
    assert "--sans: -apple-system" in RESEARCH_THEME_CSS
    assert "color-mix" not in RESEARCH_THEME_CSS
    assert "backdrop-filter" not in RESEARCH_THEME_CSS
    assert "#356f50" not in RESEARCH_THEME_CSS
    assert "--mono:" in RESEARCH_THEME_CSS
    assert "--radius: 3px;" in RESEARCH_THEME_CSS
    assert "--shadow: none;" in RESEARCH_THEME_CSS


def test_theme_embedding_requires_both_contract_markers():
    with pytest.raises(ValueError, match="exactly one"):
        embed_research_theme("<html></html>")


def test_every_example_html_app_uses_the_theme_contract():
    offenders = []
    for path in (ROOT / "examples").rglob("*.py"):
        source = path.read_text()
        if "<!doctype html" in source.lower() and "embed_research_theme" not in source:
            offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []
