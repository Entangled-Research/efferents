from __future__ import annotations

import importlib.util
import json
import stat
from pathlib import Path


def _serve_event_test_module():
    path = Path(__file__).parents[1] / "scripts" / "serve_event_test.py"
    spec = importlib.util.spec_from_file_location("serve_event_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_credentials_are_playful_and_random():
    module = _serve_event_test_module()
    first = module.generate_access_credentials()
    second = module.generate_access_credentials()

    assert first != second
    assert first["username"].count("-") == 2
    assert len(first["password"].split("-")) == 5
    assert len(first["password"].rsplit("-", 1)[1]) == 12


def test_prepare_keeps_existing_credentials_and_private_files(tmp_path):
    module = _serve_event_test_module()
    access = module.prepare(tmp_path, 8840)
    assert json.loads((tmp_path / "access.json").read_text()) == access
    assert stat.S_IMODE((tmp_path / "access.json").stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "TEST_ACCESS.md").stat().st_mode) == 0o600

    again = module.prepare(tmp_path, 8840)
    assert again == access

