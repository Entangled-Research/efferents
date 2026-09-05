from pathlib import Path
import struct


ROOT = Path(__file__).resolve().parents[1]


def test_readme_leads_with_the_interactive_research_workspace():
    readme = (ROOT / "README.md").read_text()

    assert "docs/img/local-lab-workspace.png" in readme
    assert "docs/img/lab-network-workspace.png" in readme
    assert "### Open the lab gateway" in readme
    assert "efferents serve" in readme
    assert "**Connect**" in readme
    assert "**Network**" in readme
    assert "**Steer**" in readme
    assert "VS Code" in readme
    # Publication choice stays out of the README's product story.
    assert "private by default" not in readme
    assert "public registry" not in readme


def test_readme_workspace_previews_are_wide_png_files():
    for relative_path in (
        "docs/img/local-lab-workspace.png",
        "docs/img/lab-network-workspace.png",
    ):
        preview = (ROOT / relative_path).read_bytes()
        assert preview[:8] == b"\x89PNG\r\n\x1a\n"
        width, height = struct.unpack(">II", preview[16:24])
        assert width >= 1200
        assert width / height >= 1.4
