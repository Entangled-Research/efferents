from dataclasses import replace

from efferents.agents.reviewer import Review, decide, PERSONAS
from efferents.agents.journal import write_reviews_file
from efferents.dashboard.reader import read_review_board
from efferents.journal.reviews import is_publication


def test_exactly_three_valid_independent_scores_are_required():
    reviews = [Review(p, score, "Evidence-based review", confidence=4)
               for p, score in zip(PERSONAS, [6, 7, 8])]
    assert decide(reviews, accept_mean=6, accept_min=4)["accept"]
    for invalid in (reviews[:2], reviews + reviews[:1],
                    [reviews[0], reviews[0], reviews[2]],
                    [replace(reviews[0], valid=False), *reviews[1:]],
                    [replace(reviews[0], score=3), *reviews[1:]]):
        assert not decide(invalid, accept_mean=6, accept_min=4)["accept"]


def test_persisted_board_exposes_real_scores_confidence_and_reasons(tmp_path):
    root = tmp_path / "lab"
    root.mkdir()
    assert read_review_board(root) == {"status": "awaiting paper", "scores": {}}
    reviews = [Review(p, score, "Supported by run evidence", strengths=["paired seeds"],
                      weaknesses=["narrow scope"], questions=["new domain?"], confidence=4)
               for p, score in zip(PERSONAS, [3, 6, 8])]
    decision = decide(reviews, accept_mean=6, accept_min=4)
    write_reviews_file(tmp_path / "paper/p1.reviews.md", campaign_id="p1", reviews=reviews, decision=decision)
    board = read_review_board(root)
    assert board["status"] == "rejected"
    assert board["scores"] == {"critical": 3, "neutral": 6, "optimistic": 8}
    assert board["reviews"][0]["confidence"] == 4
    assert not board["decision"]["accept"]


def test_publication_protocol_rejects_missing_or_invalid_boards():
    row = {"kind": "publication", "publication_status": "accepted", "campaign_id": "p1",
           "journal": "Methods", "review_scores": {"critical": 6, "neutral": 7, "optimistic": 8}}
    assert is_publication(row)
    assert not is_publication({**row, "kind": "discussion"})
    assert not is_publication({**row, "review_scores": {"critical": 6}})
    assert not is_publication({**row, "review_scores": {"critical": True, "neutral": 7, "optimistic": 8}})


def test_malformed_model_scores_cannot_become_passing_reviews(tmp_path, monkeypatch):
    from efferents.agents import reviewer
    paper = tmp_path / "paper.md"
    paper.write_text("A bounded test paper")
    monkeypatch.setattr(reviewer, "_prompt_for", lambda _: "Review")
    for score, confidence in [(True, 4), (11, 4), (7, None), (7, "5"), (7, 0)]:
        monkeypatch.setattr(reviewer, "parse_json_with_one_retry",
                            lambda **_: ({"score": score, "confidence": confidence}, "ok"))
        result = reviewer.review(paper_path=paper, persona="critical", client=None,
                                 budget=None, model="test")
        assert not result.valid
