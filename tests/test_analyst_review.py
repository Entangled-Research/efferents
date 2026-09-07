"""Blind qualitative review of image artifacts in the analyst digest, and the
model-client plumbing (image blocks, budget estimate, LiteLLM conversion)."""
from __future__ import annotations

import base64
import json
import sqlite3
from pathlib import Path

from efferents.agents import analyst
from efferents.agents.budget import BudgetTracker
from efferents.agents.model_client import (
    IMAGE_TOKEN_ESTIMATE,
    _convert_messages,
    _estimate_input_tokens,
    image_block,
)
from efferents.agents.state import lab_paths

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
REVIEW_JSON = json.dumps({
    "ranking": ["B", "A"],
    "notes": {"A": "repeated tiles, collapsed", "B": "varied, matches reference row"},
})


def _png(path: Path, size: int = len(PNG_BYTES)) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(PNG_BYTES + b"\x00" * max(0, size - len(PNG_BYTES)))
    return str(path)


def _rows(tmp_path: Path) -> list[dict]:
    """Two runs, latest first, with top-level + observation-level image artifacts."""
    art = tmp_path / "lab" / "artifacts"
    return [
        {
            "run_id": "run-newest-1111", "started_at": "2026-09-07T02:00:00", "synthetic_loss": 0.9,
            "artifacts_json": json.dumps([
                {"kind": "sample_grid", "path": _png(art / "run-newest-1111/sample_grid/s.png")},
                {"kind": "loss_curve", "path": _png(art / "run-newest-1111/loss_curve/c.png")},
                {"kind": "sample_grid", "path": _png(art / "run-newest-1111/sample_grid/missing.png"),
                 "missing": True},
            ]),
            "observations_json": json.dumps([
                {"name": "treatment-variant", "dimensions": {"role": "treatment"},
                 "metrics": {"synthetic_loss": 0.9},
                 "artifacts": [{"kind": "comparison_grid",
                                "path": _png(art / "run-newest-1111/comparison_grid/t.png")}]},
            ]),
        },
        {
            "run_id": "run-older-2222", "started_at": "2026-09-07T01:00:00", "synthetic_loss": 0.2,
            "artifacts_json": json.dumps([
                {"kind": "comparison_grid", "path": _png(art / "run-older-2222/comparison_grid/o.png")},
                {"kind": "sample_grid", "path": _png(art / "run-older-2222/sample_grid/big.png",
                                                     size=analyst.REVIEW_MAX_IMAGE_BYTES + 1)},
                {"kind": "sample_grid", "path": str(art / "run-older-2222/sample_grid/nope.png")},
            ]),
            "observations_json": "[]",
        },
    ]


def test_collect_prefers_comparison_then_sample_grids_latest_first(tmp_path):
    images = analyst.collect_review_images(_rows(tmp_path), limit=10)
    assert [(i["run_id"], i["kind"]) for i in images] == [
        ("run-newest-1111", "comparison_grid"),
        ("run-older-2222", "comparison_grid"),
        ("run-newest-1111", "sample_grid"),
        ("run-newest-1111", "loss_curve"),  # other image kinds fill remaining slots
    ]
    assert images[0]["observation"] == "treatment-variant"
    # Missing, oversized and non-existent files were skipped.
    assert all(Path(i["path"]).is_file() for i in images)
    assert analyst.collect_review_images(_rows(tmp_path), limit=2)[1]["run_id"] == "run-older-2222"


def test_env_limit_zero_disables(tmp_path, monkeypatch, fake_anthropic_factory):
    monkeypatch.setenv(analyst.REVIEW_IMAGES_ENV, "0")
    assert analyst.review_image_limit() == 0
    client = fake_anthropic_factory([REVIEW_JSON])
    budget = BudgetTracker(tmp_path / "budget.jsonl", daily_cap_usd=5.0)
    assert analyst.blind_image_review(rows=_rows(tmp_path), budget=budget, client=client, model="m") is None
    assert client.calls == []
    monkeypatch.setenv(analyst.REVIEW_IMAGES_ENV, "junk")
    assert analyst.review_image_limit() == analyst.REVIEW_IMAGES_DEFAULT


def test_review_is_blind_and_unblinds_in_section(tmp_path, fake_anthropic_factory):
    client = fake_anthropic_factory([REVIEW_JSON])
    budget = BudgetTracker(tmp_path / "budget.jsonl", daily_cap_usd=5.0)
    section = analyst.blind_image_review(
        rows=_rows(tmp_path), budget=budget, client=client, model="fake-model", limit=2
    )

    assert len(client.calls) == 1
    call = client.calls[0]
    content = call["messages"][0]["content"]
    images = [b for b in content if b["type"] == "image"]
    assert len(images) == 2
    assert images[0]["source"] == {
        "type": "base64", "media_type": "image/png",
        "data": base64.standard_b64encode(PNG_BYTES).decode("ascii"),
    }
    prompt = json.dumps([call.get("system"), call["messages"]])
    for leaked in ("run-newest", "run-older", "1111", "2222", "treatment", "treatment-variant",
                   "synthetic_loss", "0.9", "comparison_grid", "sample_grid", ".png"):
        assert leaked not in prompt, leaked
    assert "Image A:" in prompt and "Image B:" in prompt
    assert "reference row" in prompt and "collapse" in prompt

    assert section.startswith("## Qualitative review (blind)")
    lines = section.splitlines()
    assert "| 1 | B | run-older-2222 | — | comparison_grid | varied, matches reference row |" in lines
    assert "| 2 | A | run-newest-1111 | treatment-variant | comparison_grid | repeated tiles, collapsed |" in lines
    assert budget.spend_total() >= 0.0  # the call was recorded against the ledger


def test_review_skips_without_images_or_budget(tmp_path, fake_anthropic_factory):
    client = fake_anthropic_factory([REVIEW_JSON])
    assert analyst.blind_image_review(
        rows=[{"run_id": "r"}], budget=BudgetTracker(tmp_path / "b.jsonl", daily_cap_usd=5.0),
        client=client, model="m",
    ) is None
    exhausted = BudgetTracker(tmp_path / "b2.jsonl", daily_cap_usd=0.0)
    assert analyst.blind_image_review(rows=_rows(tmp_path), budget=exhausted, client=client, model="m") is None
    assert client.calls == []


def test_unparseable_review_still_renders_images(tmp_path, fake_anthropic_factory):
    client = fake_anthropic_factory(["I cannot rank these."])
    budget = BudgetTracker(tmp_path / "budget.jsonl", daily_cap_usd=5.0)
    section = analyst.blind_image_review(rows=_rows(tmp_path), budget=budget, client=client, model="m", limit=1)
    assert "| unranked | A | run-newest-1111 |" in section
    assert "no parseable ranking" in section


def test_write_digest_makes_one_extra_call_and_embeds_section(tmp_path, fake_anthropic_factory):
    paths = lab_paths(tmp_path / "lab")
    paths.digests_dir.mkdir(parents=True)
    conn = sqlite3.connect(paths.runs_db)
    conn.execute(
        "CREATE TABLE runs (run_id TEXT PRIMARY KEY, started_at TEXT, status TEXT, "
        "synthetic_loss REAL, artifacts_json TEXT, observations_json TEXT)"
    )
    for r in _rows(tmp_path):
        conn.execute("INSERT INTO runs VALUES (?, ?, 'succeeded', ?, ?, ?)",
                     (r["run_id"], r["started_at"], r["synthetic_loss"],
                      r["artifacts_json"], r["observations_json"]))
    conn.commit()
    conn.close()

    client = fake_anthropic_factory([REVIEW_JSON, "# Digest\n\n## TL;DR\n- cites review\n"])
    out = analyst.write_digest(
        paths=paths, context_dir=tmp_path / "context",
        budget=BudgetTracker(paths.budget, daily_cap_usd=5.0),
        client=client, model="fake-model", notify=False,
    )
    assert len(client.calls) == 2
    digest = Path(out["path"]).read_text()
    assert "## Qualitative review (blind)" in digest
    assert digest.index("## Evidence (computed)") < digest.index("## Qualitative review") < digest.index("cites review")
    # The narrative prompt saw the un-blinded review; the review call did not see runs.
    assert "## Qualitative review (blind)" in json.dumps(client.calls[1]["messages"])
    assert "run-newest" not in json.dumps(client.calls[0]["messages"])


def test_image_block_and_budget_estimate_count_images_not_bytes():
    block = image_block(b"\x00" * 3_000_000, "image/png")
    assert block["type"] == "image" and block["source"]["type"] == "base64"
    messages = [{"role": "user", "content": [block, {"type": "text", "text": "rank"}]}]
    estimate = _estimate_input_tokens({"messages": messages})
    assert IMAGE_TOKEN_ESTIMATE <= estimate < IMAGE_TOKEN_ESTIMATE + 200
    assert _estimate_input_tokens({"messages": [{"role": "user", "content": "rank"}]}) < IMAGE_TOKEN_ESTIMATE


def test_litellm_conversion_carries_images_as_data_urls():
    block = image_block(b"abc", "image/png")
    converted = _convert_messages([{"role": "user", "content": [{"type": "text", "text": "hi"}, block]}])
    assert converted == [{"role": "user", "content": [
        {"type": "text", "text": "hi"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + block["source"]["data"]}},
    ]}]
    # Text-only content keeps the plain-string shape other tests rely on.
    assert _convert_messages([{"role": "user", "content": [{"type": "text", "text": "hi"}]}]) == [
        {"role": "user", "content": "hi"}
    ]
