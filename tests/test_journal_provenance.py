from pathlib import Path

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
