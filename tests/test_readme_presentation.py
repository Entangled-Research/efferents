from pathlib import Path
import struct


ROOT = Path(__file__).resolve().parents[1]


def test_readme_follows_connect_network_audit_progression():
    readme = (ROOT / "README.md").read_text()

    connect = readme.index("## 1 · Connect a lab")
    network = readme.index("## 2 · The lab network")
    audit = readme.index("## 3 · Audit a lab")
    assert connect < network < audit
    assert "docs/img/lab-network-demo.gif" in readme
    assert "efferents serve" in readme
    assert "VS Code" in readme
    # Publication choice stays out of the README's product story, and the
    # retired navy-theme screenshots stay gone.
    assert "private by default" not in readme
    assert "public registry" not in readme
    assert "local-lab-workspace" not in readme
    assert "demo-dashboard" not in readme


def test_readme_demo_is_a_wide_looping_gif():
    preview = (ROOT / "docs/img/lab-network-demo.gif").read_bytes()

    assert preview[:6] in (b"GIF87a", b"GIF89a")
    width, height = struct.unpack("<HH", preview[6:10])
    assert width >= 900
    assert width / height >= 1.4
    assert b"NETSCAPE2.0" in preview
