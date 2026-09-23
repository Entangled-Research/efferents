from efferents.journal.provenance import received_publications, record_execution, campaign_citations, citation_markdown
from efferents.journals import journal_for_domain


def _received(root):
    paper = root.parent / "paper"
    paper.mkdir()
    (paper / "external_journal.md").write_text(
        "## 2026-09-23 10:00 UTC — source-campaign\n**Lab**: physics-lab\n"
        "**Headline**: Bounded inverse model\n**Scores**: critical=7, neutral=7, optimistic=8\n"
        "**Journal**: Physics & Dynamics\n**Domain**: physics\n\n"
        "### Accepted manuscript\n\n> ## Methods\n> Paired validation.\n"
    )


def test_only_completed_cited_runs_become_use_and_paper_citations(tmp_path):
    root = tmp_path / "lab"
    root.mkdir()
    _received(root)
    source = "journal:physics-lab:source-campaign"
    assert source in received_publications(root)
    assert not campaign_citations(root, "chem-campaign")  # subscription is not use
    proposal = {"name": "heldout-ranking", "campaign_id": "chem-campaign", "student_id": "primary",
                "external_citations": [{"publication_id": source, "why": "Use its paired validation design"}]}
    assert record_execution(root, proposal, {"ok": False}) == 0
    outcome = {"ok": True, "rows": [{"run_id": "chem-run"}]}
    assert record_execution(root, proposal, outcome) == 1
    assert record_execution(root, proposal, outcome) == 0
    records = campaign_citations(root, "chem-campaign")
    assert len(records) == 1 and records[0]["reproduction_status"] == "unverified"
    markdown = citation_markdown(records)
    assert source in markdown and "chem-run" in markdown and "Physics & Dynamics" in markdown
    assert "Journal use:" in (root / "lab_notebook.md").read_text()
    assert not campaign_citations(root, "unrelated-campaign")
    proposal["external_citations"] = [{"publication_id": "invented", "why": "Invalid"}]
    assert record_execution(root, proposal, outcome) == 0


def test_incomplete_boards_and_legacy_direct_messages_cannot_be_cited(tmp_path):
    root = tmp_path / "lab"
    root.mkdir()
    _received(root)
    path = tmp_path / "paper" / "external_journal.md"
    path.write_text(path.read_text().replace("neutral=7, ", ""))
    assert received_publications(root) == {}


def test_chemistry_has_its_own_stable_journal():
    assert journal_for_domain("chemistry") == "Chemistry"
    assert journal_for_domain("organic-chemistry") == "Chemistry"
    assert journal_for_domain("chemical-reaction-mechanisms") == "Chemistry"
    assert journal_for_domain("cheminformatics") == "Chemistry"
    assert journal_for_domain("physics") != "Chemistry"


def test_received_paper_flows_through_real_execution_into_reviewed_citation(
    tmp_path, monkeypatch, fake_anthropic_factory
):
    """Exercise actual bounded execution and writer artifacts; only model calls are fake."""
    import shutil
    import sys
    from pathlib import Path
    import yaml
    from efferents import lab
    from efferents.agents import executor, federation, journal, rebuttal, reviewer, writer
    from efferents.agents.budget import BudgetTracker
    from efferents.agents.state import init_lab, lab_paths
    from efferents.journal.provenance import publication_digest
    from efferents.lab import LabConfig

    sub = tmp_path / "chemistry"
    shutil.copytree(Path(__file__).parents[1] / "examples/smoke-lab", sub,
                    ignore=shutil.ignore_patterns("lab", "__pycache__"))
    config_file = sub / "lab.yaml"
    config_file.write_text(config_file.read_text().replace("python3 -m", f"{sys.executable} -m"))
    lab.set_config(LabConfig.from_submission(sub))
    monkeypatch.setattr(lab, "PEER_REVIEW_ENABLED", True)
    paths = lab_paths(sub / "lab")
    init_lab(paths)
    from efferents.migrations.runner import ensure_runs_table
    ensure_runs_table(paths.runs_db, lab.get_config())
    _received(paths.root)
    source_path = sub / "paper/external_journal.md"
    source_text = source_path.read_text()
    source_path.unlink()
    delivered = tmp_path / "delivered.md"
    delivered.write_text(source_text)
    before = paths.budget.read_bytes()
    assert federation.consume_external_journal(source=delivered, out_path=source_path,
                                               our_lab_id=lab.LAB_ID)["n_added"] == 1
    assert paths.budget.read_bytes() == before  # delivery causes no model spending
    publication_id = "journal:physics-lab:source-campaign"
    assert not campaign_citations(paths.root, "c1")
    assert not federation.is_reproduced(sub / "paper", lab_id="physics-lab", campaign_id="source-campaign")

    foundation = {"name": "blocked-premise", "campaign_id": "c1",
                  "foundational_external": [{"lab_id": "physics-lab", "campaign_id": "source-campaign",
                                             "why": "Requires the external claim"}],
                  "config_overrides": {"coefficient": 0.7}}
    blocked = executor.execute(paths=paths, proposal=foundation)
    assert blocked["blocked"] and not blocked["ok"]
    assert record_execution(paths.root, foundation, blocked) == 0
    baseline = executor.execute(paths=paths, proposal={"name": "baseline", "campaign_id": "baseline",
                                                       "config_overrides": {"coefficient": 0.5}})
    proposal = {"name": "candidate", "campaign_id": "c1", "student_id": "primary",
                "config_overrides": {"coefficient": 0.7},
                "external_citations": [{"publication_id": publication_id,
                                        "why": "Adopt its paired validation design, without assuming its result"}]}
    outcome = executor.execute(paths=paths, proposal=proposal)
    assert baseline["ok"] and outcome["ok"]
    assert record_execution(paths.root, proposal, outcome) == 1
    use = campaign_citations(paths.root, "c1")[0]
    assert use["source_sha256"] == publication_digest(source_text)
    assert use["reproduction_status"] == "unverified"

    def review_paper(*, persona, **kwargs):
        review = reviewer.Review(persona, 8, "Measured comparison", confidence=4)
        if hasattr(review, "material_flaw"):
            review.material_flaw = False
        return review
    monkeypatch.setattr(reviewer, "review", review_paper)
    monkeypatch.setattr(rebuttal, "write_rebuttal", lambda **kw: "## Rebuttal\nMeasured comparison retained.")
    monkeypatch.setattr(journal, "auto_commit_paper", lambda **kw: None)
    body = "\n".join(f"## {section}\n\nMeasured comparison with bounded scope.\n"
                     for section in ("Motivation", "Methods", "Results", "Conclusion", "Next questions"))
    client = fake_anthropic_factory([body])
    budget = BudgetTracker(paths.budget, daily_cap_usd=2)
    artifact = writer.write_phase_a_paper(
        writer.writer_paths(lab=paths.root, paper=sub / "paper", reports=sub / "reports", context=sub / "context"),
        {"id": "c1", "question": "Improve a bounded test using the cited validation design",
         "hypothesis_path": "popper-corpus/c1/hypothesis.md", "hypothesis_hash": "sha256:" + "0" * 64},
        client, budget=budget,
    )
    assert artifact and artifact == (sub / "paper/c1.md").read_text()
    metadata = yaml.safe_load(artifact.split("---", 2)[1])
    citation = metadata["external_journal_citations"][0]
    assert citation["publication_id"] == publication_id
    assert citation["run_ids"] == [outcome["rows"][0]["run_id"]]
    assert citation["reproduction_status"] == "unverified"
    assert "## External journal citations" in artifact and publication_id in artifact
    assert "is not independent corroboration" in artifact
    assert "c1" in (sub / "paper/journal.md").read_text()
    assert "Journal use:" in paths.notebook.read_text()
    assert budget.spend_total() > 0  # writer charges the same lab ledger
