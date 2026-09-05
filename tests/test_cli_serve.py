import efferents.cli as cli


def test_serve_subcommand_parses():
    parser = cli.build_parser()
    args = parser.parse_args(["serve", "--lab-root", "lab", "--port", "9001", "--no-open"])
    assert args.func is cli._cmd_serve
    assert args.lab_root == "lab"
    assert args.port == 9001
    assert args.no_open is True
    assert args.paused_demo is False


def test_serve_defaults():
    parser = cli.build_parser()
    args = parser.parse_args(["serve"])
    assert args.lab_root == "lab"
    assert args.port == 8800
    assert args.no_open is False
    assert args.paused_demo is False


def test_serve_paused_demo_parses():
    args = cli.build_parser().parse_args(["serve", "--paused-demo"])
    assert args.paused_demo is True


def test_cmd_serve_loads_config_and_starts(tmp_path, monkeypatch):
    (tmp_path / "hypothesis.md").write_text(
        "---\nslug: t\nfalsifiability_gate: passed\nstatus: active\n---\n# H\n"
    )
    (tmp_path / "lab.yaml").write_text(
        "lab_id: t\ndomain: d\n"
        "source:\n  dir: ./src/\n"
        "executor:\n  run_command: 'python -m x --config {config_path}'\n"
        "  config_template: default.yaml\n"
        "metrics:\n  headline:\n    column: loss\n    direction: min\n"
        "  panels:\n    - { column: loss, label: Loss }\n"
        "budget:\n  daily_cap_usd: 1.0\n"
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "default.yaml").touch()

    called = {}

    def fake_serve(lab_root, port, open_browser, *, paused_demo):
        called["lab_root"] = str(lab_root)
        called["port"] = port
        called["open_browser"] = open_browser
        called["paused_demo"] = paused_demo

    monkeypatch.setattr("efferents.dashboard.server.serve", fake_serve)

    import argparse
    args = argparse.Namespace(lab_root=str(tmp_path), port=8800, no_open=True)
    rc = cli._cmd_serve(args)
    assert rc == 0
    assert called["port"] == 8800
    assert called["open_browser"] is False
    assert called["paused_demo"] is False


def test_cmd_serve_starts_entry_page_without_existing_lab(tmp_path, monkeypatch):
    called = {}

    def fake_serve(lab_root, port, open_browser, *, paused_demo):
        called["lab_root"] = lab_root
        called["port"] = port
        called["paused_demo"] = paused_demo

    monkeypatch.setattr("efferents.dashboard.server.serve", fake_serve)

    import argparse
    args = argparse.Namespace(
        lab_root=str(tmp_path / "not-connected"),
        port=8800,
        no_open=True,
    )
    rc = cli._cmd_serve(args)

    assert rc == 0
    assert called["lab_root"] is None
