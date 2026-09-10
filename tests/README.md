# efferents tests

Tests exercise lab-agnostic framework behavior and the maintained smoke lab.

- `tests/test_*.py` — generic framework tests. Run against the
  `smoke_lab_config` fixture in `conftest.py`. Must pass without data or code
  from the original reference lab.
- `tests/integration/test_smoke_lab_e2e.py` — end-to-end test against
  `examples/smoke-lab/`. Marked `@pytest.mark.integration`; opt in via
  `pytest -m integration`.

## Running

- All generic + smoke tests: `uv run pytest tests/`
- Integration only: `uv run pytest -m integration`
