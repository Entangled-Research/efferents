from pathlib import Path
import struct


ROOT = Path(__file__).resolve().parents[1]


def test_readme_follows_connect_network_audit_progression():
    readme = (ROOT / "README.md").read_text()

    connect = readme.index("## 1 · Connect a lab")
    network = readme.index("## 2 · The lab network")
    audit = readme.index("## 3 · Audit a lab")
    assert connect < network < audit
    assert "docs/img/connect-a-lab.png" in readme
    assert "docs/img/lab-network.png" in readme
    assert "docs/img/audit-a-lab.png" in readme
    assert "efferents serve" in readme
    assert "VS Code" in readme
    # Publication choice stays out of the README's product story, and the
    # retired navy-theme screenshots stay gone.
    assert "private by default" not in readme
    assert "public registry" not in readme
    assert "local-lab-workspace" not in readme
    assert "demo-dashboard" not in readme


def test_readme_workspace_previews_are_wide_png_files():
    for relative_path in (
        "docs/img/connect-a-lab.png",
        "docs/img/lab-network.png",
        "docs/img/audit-a-lab.png",
    ):
        preview = (ROOT / relative_path).read_bytes()
        assert preview[:8] == b"\x89PNG\r\n\x1a\n"
        width, height = struct.unpack(">II", preview[16:24])
        assert width >= 1200
        assert width / height >= 1.4
