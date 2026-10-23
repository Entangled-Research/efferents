"""popper_gate.run runs single-shot self-play and validates output."""
from __future__ import annotations

from pathlib import Path

import pytest

from efferents.agents.popper_gate import GateResult, run_gate


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def popper_repo(monkeypatch, tmp_path):
    # Deliberately absent: production refinement must not read this location.
    missing = tmp_path / "no-external-probe"
    monkeypatch.setenv("POPPER_PROBE_REPO", str(missing))
    return missing


def test_accept_path_writes_file_and_returns_hash(
    popper_repo, tmp_path, fake_anthropic_factory
):
    valid_text = (FIXTURES / "valid_hypothesis.md").read_text()
    client = fake_anthropic_factory([valid_text])
    out_root = tmp_path / "popper-corpus"

    result = run_gate(
        draft_claim="aug_depth=3 should reduce W1 by 10%",
        slug="aug-depth-three",
        corpus_root=out_root,
        client=client,
    )

    assert isinstance(result, GateResult)
    assert result.ok
    assert result.path == out_root / "aug-depth-three/hypothesis.md"
    assert result.path.exists()
    assert result.path.read_text() == valid_text
    assert result.hash.startswith("sha256:")
    assert len(result.hash) == len("sha256:") + 64


def test_reject_then_drop_after_one_retry(
    popper_repo, tmp_path, fake_anthropic_factory
):
    invalid_text = (FIXTURES / "invalid_hypothesis.md").read_text()
    client = fake_anthropic_factory([invalid_text, invalid_text])
    out_root = tmp_path / "popper-corpus"

    result = run_gate(
        draft_claim="something fuzzy",
        slug="fuzzy",
        corpus_root=out_root,
        client=client,
    )

    assert not result.ok
    assert result.reason
    assert "validate" in result.reason.lower() or "schema" in result.reason.lower()
    # The model was retried once → 2 calls total
    assert len(client.calls) == 2


def test_retry_succeeds(popper_repo, tmp_path, fake_anthropic_factory):
    invalid_text = (FIXTURES / "invalid_hypothesis.md").read_text()
    valid_text = (FIXTURES / "valid_hypothesis.md").read_text()
    client = fake_anthropic_factory([invalid_text, valid_text])
    out_root = tmp_path / "popper-corpus"

    result = run_gate(
        draft_claim="retry case",
        slug="retry-case",
        corpus_root=out_root,
        client=client,
    )

    assert result.ok
    assert len(client.calls) == 2
    # On retry, the user message must include the validator's errors so the
    # model can correct course
    second_user_msgs = client.calls[1].get("messages", [])
    assert any("ERROR" in str(m) or "validator" in str(m).lower() for m in second_user_msgs)


# ---------------------------------------------------------------- charter ----

def test_write_charter_creates_then_appends(tmp_path):
    from efferents.agents.popper_gate import CHARTER_FILENAME, write_charter

    ctx = tmp_path / "context"
    charter = write_charter(
        ctx,
        initial_direction="Detect tipping points earlier\nwith AC1 alarms.",
        prompted_by="funder:masha",
        hypothesis_path="popper-corpus/tipping/hypothesis.md",
        hypothesis_hash="sha256:abc123",
        design_notes="Rejected variance-only indicator; chose lag-1 AC.",
        title="initial direction",
    )
    assert charter == ctx / CHARTER_FILENAME
    text = charter.read_text()
    # Framing: living document, guidance not rules, verbatim direction kept.
    assert "guidance, not rules" in text
    assert "never rewrite earlier entries" in text
    assert "> Detect tipping points earlier" in text
    assert "> with AC1 alarms." in text
    assert "funder:masha" in text
    assert "sha256:abc123" in text

    write_charter(
        ctx,
        initial_direction="Second campaign: storm-trend features.",
        prompted_by="student:s2",
        title="campaign gate: storm-trend",
    )
    text2 = charter.read_text()
    # Append-only: first entry intact, second added, header not duplicated.
    assert "> Detect tipping points earlier" in text2
    assert "campaign gate: storm-trend" in text2
    assert text2.count("# Lab charter") == 1


def test_gate_pass_appends_charter_when_charter_dir_given(
    popper_repo, tmp_path, fake_anthropic_factory
):
    valid_text = (FIXTURES / "valid_hypothesis.md").read_text()
    client = fake_anthropic_factory([valid_text])

    result = run_gate(
        draft_claim="my draft direction, verbatim",
        slug="charter-case",
        corpus_root=tmp_path / "popper-corpus",
        client=client,
        charter_dir=tmp_path / "context",
        prompted_by="student:default",
    )

    assert result.ok
    charter = (tmp_path / "context" / "popper.md").read_text()
    assert "> my draft direction, verbatim" in charter
    assert "student:default" in charter
    assert result.hash in charter


def test_read_context_includes_charter(tmp_path):
    from efferents.agents.popper_gate import write_charter
    from efferents.agents.state import read_context

    write_charter(tmp_path, initial_direction="direction", prompted_by="funder")
    ctx = read_context(tmp_path)
    assert "popper.md" in ctx
    assert "guidance, not rules" in ctx["popper.md"]


def test_static_block_carries_charter_as_guidance(tmp_path, monkeypatch):
    from efferents.agents.researcher import _shared_static_block

    block = _shared_static_block(
        vision="v", decisions="d", charter="### 2026-07-24 — initial direction"
    )
    assert "Lab charter (popper.md)" in block
    assert "not binding rules" in block
    assert "### 2026-07-24 — initial direction" in block
    # Absent charter adds no empty section.
    assert "Lab charter" not in _shared_static_block(vision="v", decisions="d")


def test_failed_contract_is_not_accepted(tmp_path, fake_anthropic_factory):
    text = "---\nslug: unclear\nstatus: unfalsifiable\nfalsifiability_gate: failed\n---\n\n## Diagnostic\nThe objective is missing.\n"
    result = run_gate(draft_claim="unclear", slug="unclear", corpus_root=tmp_path,
                      client=fake_anthropic_factory([text]))
    assert not result.ok and "objective is missing" in result.reason
    assert not (tmp_path / "unclear/hypothesis.md").exists()


def test_internal_refinement_does_not_require_a_checkout(tmp_path, monkeypatch):
    from efferents.agents.popper_gate import _skill_md, validate_hypothesis_text, INTERACTIVE_INSTRUCTION
    monkeypatch.setenv("POPPER_PROBE_REPO", str(tmp_path / "absent"))
    ok, _ = validate_hypothesis_text((FIXTURES / "valid_hypothesis.md").read_text(), tmp_path / "scratch")
    assert ok
    assert "routine methodological choices yourself" in _skill_md()
    assert "Do not ask for routine approval" in INTERACTIVE_INSTRUCTION


def test_internal_validator_accepts_title_case_section_headings(tmp_path):
    from efferents.agents.popper_gate import validate_hypothesis_text
    text=(FIXTURES / "valid_hypothesis.md").read_text().replace("## Operational restatement", "## Operational Restatement").replace("## Test design", "## Test Design")
    assert validate_hypothesis_text(text,tmp_path)[0]


def test_refinement_uses_configured_supervisor_instead_of_legacy_provider(tmp_path, monkeypatch, fake_anthropic_factory):
    monkeypatch.setenv('EFFERENTS_MODEL', 'openai/gpt-6.1-sol')
    monkeypatch.setenv('EFFERENTS_MODEL_SUPERVISOR', 'openai/gpt-6.1-sol')
    client = fake_anthropic_factory([(FIXTURES / 'valid_hypothesis.md').read_text()])
    result = run_gate(draft_claim='bounded comparison', slug='aug-depth-three', corpus_root=tmp_path, client=client)
    assert result.ok
    assert client.calls[0]['model'] == 'openai/gpt-6.1-sol'
    assert client.calls[0]['max_tokens'] >= 8192
