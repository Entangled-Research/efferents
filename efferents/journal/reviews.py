"""Read persisted review boards without running reviewers or inventing scores."""
from __future__ import annotations

import re

PERSONAS = ("critical", "neutral", "optimistic")


def review_scores(markdown: str) -> dict[str, int]:
    """Support current board tables and historical journal score summaries."""
    scores = {}
    for persona, value in re.findall(
        r"(?:\|\s*|\b)(critical|neutral|optimistic|enthusiast)(?:\s*\|\s*|=)(\d{1,2})(?=\s|[,|)]|$)",
        markdown,
    ):
        if 1 <= int(value) <= 10:
            scores["optimistic" if persona == "enthusiast" else persona] = int(value)
    return scores


def is_publication(row: dict) -> bool:
    scores = row.get("review_scores")
    return (row.get("kind") == "publication" and row.get("publication_status") == "accepted"
            and isinstance(row.get("campaign_id"), str) and bool(row["campaign_id"])
            and isinstance(scores, dict) and set(scores) == set(PERSONAS)
            and all(type(score) is int and 1 <= score <= 10 for score in scores.values())
            and isinstance(row.get("journal"), str) and bool(row["journal"]))
