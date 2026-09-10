"""The repo-adapter runner executes train/eval and writes provenance artifacts."""
import json
import shutil
from pathlib import Path


from efferents.runner import run_adapter

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "repo-adapter"


def _copy_repo(tmp_path: Path) -> Path:
    dst = tmp_path / "repo"
    shutil.copytree(EXAMPLE, dst)
    return dst


def test_run_writes_all_artifacts(tmp_path):
    out = run_adapter(EXAMPLE, tmp_path / "out", approved=True)
    for name in ("001_hypothesis.md", "002_experiment_plan.md",
                 "003_results.md", "004_research_memo.md"):
        assert (out / "journal" / name).is_file()
    assert (out / "runs.jsonl").is_file()
    assert (out / "claims.jsonl").is_file()
    assert (out / "dashboard.html").is_file()


def test_run_finds_interior_optimum(tmp_path):
    out = run_adapter(EXAMPLE, tmp_path / "out", approved=True)
    runs = [
        json.loads(line)
        for line in (out / "runs.jsonl").read_text().splitlines()
    ]
    assert len(runs) == 5
    by_val = {r["value"]: r["val_f1"] for r in runs}
    assert by_val[0.65] == 0.8889  # the deterministic interior peak
    best = max(runs, key=lambda r: r["val_f1"])
    assert best["value"] == 0.65


def test_run_claims_reference_real_runs(tmp_path):
    out = run_adapter(EXAMPLE, tmp_path / "out", approved=True)
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


def test_run_does_not_pollute_repo(tmp_path):
    repo = _copy_repo(tmp_path)
    before = {p.name for p in repo.iterdir()}
    run_adapter(repo, tmp_path / "out", approved=True)
    after = {p.name for p in repo.iterdir()}
    assert before == after  # checkpoints/configs land under out/, not the repo


def test_max_iters_caps_runs(tmp_path):
    out = run_adapter(EXAMPLE, tmp_path / "out", max_iters=2, approved=True)
    runs = (out / "runs.jsonl").read_text().splitlines()
    assert len(runs) == 2


def test_dry_run_writes_plan_but_no_runs(tmp_path):
    repo = _copy_repo(tmp_path)
    yaml_path = repo / "efferents.yaml"
    yaml_path.write_text(
        yaml_path.read_text().replace('mode: "plan_then_execute"', 'mode: "dry_run"'))
    out = run_adapter(repo, tmp_path / "out")
    assert (out / "journal" / "002_experiment_plan.md").is_file()
    assert not (out / "runs.jsonl").exists()


def test_plan_then_execute_requires_explicit_approval(tmp_path):
    out = run_adapter(EXAMPLE, tmp_path / "out")
    plan = (out / "journal" / "002_experiment_plan.md").read_text()
    assert "execution_authorized: false" in plan
    assert not (out / "runs.jsonl").exists()


def test_memo_has_evidence_table(tmp_path):
    out = run_adapter(EXAMPLE, tmp_path / "out", approved=True)
    memo = (out / "journal" / "004_research_memo.md").read_text()
    for section in ("## Summary", "## Hypothesis", "## Experiment plan",
                    "## Results", "## Automated audit notes", "## Limitations",
                    "## Next experiment", "## Evidence table"):
        assert section in memo
    assert "review_status: not_peer_reviewed" in memo
    assert "Board decision" not in memo
    assert "2026-06-25T09:00:00Z" not in memo
