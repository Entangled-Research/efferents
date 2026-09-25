"""Execute the browser's publication renderer against scientific manuscript text."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.fixture(scope="module")
def render():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is needed to execute the browser renderer")
    source = (Path(__file__).resolve().parents[1] / "efferents/dashboard/static/dashboard.js").read_text()
    # Load the actual pure helpers without starting the browser application.
    helpers = source[source.index("function esc(value)"):source.index("function text(")]
    helpers += source[source.index("function renderPublicationInline(value)"):source.index("function networkDialog(")]

    def run(markdown):
        result = subprocess.run(
            [node, "-e", helpers + "\nprocess.stdout.write(renderMarkdownSafe(JSON.parse(process.argv[1])));", json.dumps(markdown)],
            check=True, text=True, capture_output=True,
        )
        return result.stdout

    return run


def test_scientific_cases_and_reproduction_command(render):
    # Synthetic numerical-method manuscript; no participant submission.
    result = render(
        "The cases are `x**3` with `1/4`, `math.sin` with `1-math.cos(1)`, "
        "and `math.exp` with `math.e-1`. The run config's `seed` chooses by `seed % 3`. "
        "Each absolute error is `abs(estimate - exact)`. The falsifier is **any** "
        "Simpson error above `1e-4`.\n\n"
        "Run `python3 example-verification.source.py --config ../lab/configs/run_<run_id>.yaml` "
        "from `paper/`; artifacts stay under `lab/artifacts/<run_id>/`."
    )
    for literal in ("x**3", "1/4", "math.sin", "1-math.cos(1)", "math.exp", "math.e-1", "seed % 3", "abs(estimate - exact)"):
        assert f"<code>{literal}</code>" in result
    assert "<strong>any</strong>" in result
    assert "run_&lt;run_id&gt;.yaml</code>" in result
    assert "<code>lab/artifacts/&lt;run_id&gt;/</code>" in result
    assert "`" not in result


def test_inline_markup_in_all_manuscript_blocks(render):
    result = render("---\nlab_id: hidden\n---\n## **Methods**\n\n- **Bound:** `1e-4`\n1. *Reproduce*\n\n| Case | Error |\n| --- | --- |\n| `x**3` | **zero** |")
    assert "lab_id" not in result
    assert "<h3><strong>Methods</strong></h3>" in result
    assert "<li><strong>Bound:</strong> <code>1e-4</code></li>" in result
    assert "<li><em>Reproduce</em></li>" in result
    assert "<td><code>x**3</code></td><td><strong>zero</strong></td>" in result


def test_code_and_explicit_escapes_are_not_reinterpreted(render):
    result = render("`**literal** _x_ <run_id>` and ``a `quoted` value``\n\n" + r"\*\*literal\*\* and run\_\<run\_id>" + "\n\n```python\nx**3 < 4\n**literal**\n```")
    assert "<code>**literal** _x_ &lt;run_id&gt;</code>" in result
    assert "<code>a `quoted` value</code>" in result
    assert "**literal** and run_&lt;run_id&gt;" in result
    assert "<pre><code>x**3 &lt; 4\n**literal**</code></pre>" in result
    assert "<strong>" not in result


def test_untrusted_manuscript_never_creates_active_html(render):
    result = render('<script>alert(1)</script> **<img src=x onerror=alert(1)>** `</code><svg onload=alert(1)>` [click](javascript:alert(1))')
    for tag in ("<script", "<img", "<svg", "<a "):
        assert tag not in result
    assert "<strong>&lt;img" in result
    assert "<code>&lt;/code&gt;&lt;svg" in result
