"""The offline demo must produce the expected artifacts with no API call."""
import json

from efferents.demo import run_demo


def test_demo_writes_all_artifacts(tmp_path):
    out = run_demo("smoke-lab", tmp_path / "out")

    journal = out / "journal"
    for name in (
        "001_hypothesis.md",
        "002_experiment_plan.md",
        "003_results.md",
        "004_reviewed_memo.md",
    ):
        assert (journal / name).is_file(), f"missing {name}"

    assert (out / "runs.jsonl").is_file()
    assert (out / "claims.jsonl").is_file()
    assert (out / "dashboard.html").is_file()


def test_demo_runs_jsonl_is_valid_and_nonempty(tmp_path):
    out = run_demo("smoke-lab", tmp_path / "out")
    lines = (out / "runs.jsonl").read_text().strip().splitlines()
    assert len(lines) == 5
    for line in lines:
        row = json.loads(line)
        assert "run_id" in row
        assert "synthetic_loss" in row
        assert isinstance(row["synthetic_loss"], (int, float))


def test_demo_memo_has_evidence_table_with_run_ids(tmp_path):
    out = run_demo("smoke-lab", tmp_path / "out")
    memo = (out / "journal" / "004_reviewed_memo.md").read_text()
    for section in (
        "## Summary", "## Hypothesis", "## Experiment plan", "## Results",
        "## Reviewer notes", "## Limitations", "## Next experiment",
        "## Evidence table",
    ):
        assert section in memo, f"memo missing {section}"
    # every claims.jsonl run-backed claim must reference a real run id
    run_ids = {
        json.loads(line)["run_id"]
        for line in (out / "runs.jsonl").read_text().splitlines()
    }
    claims = [
        json.loads(line)
        for line in (out / "claims.jsonl").read_text().splitlines()
    ]
    for c in claims:
        if c["run_id"] is not None:
            assert c["run_id"] in run_ids


def test_demo_is_deterministic(tmp_path):
    a = run_demo("smoke-lab", tmp_path / "a")
    b = run_demo("smoke-lab", tmp_path / "b")
    assert (a / "runs.jsonl").read_text() == (b / "runs.jsonl").read_text()
