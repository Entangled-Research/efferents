"""Librarian agent: literature-review sub-tool callable from Researcher/Coder.

Cache-first lit-review with a SQLite-backed knowledge base at
`lab/knowledge/kb.sqlite`. On a cache miss, calls Anthropic with server-side
web_search to synthesize a topic summary, persist papers to kb_papers, and
return a LitResult.

Two tables:
    kb_topics  — one row per (normalized_topic, intent). Holds the synthesis
                 prose + bridges + paper list (denormalized JSON).
    kb_papers  — one row per bib_key. The Writer reads this to build refs.bib.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import anthropic

from efferents.agents.budget import BudgetTracker, CallUsage, billing_model, model_for
from efferents.agents.prompts.loader import load_prompt
from efferents.agents.state import LabPaths, append_jsonl, parse_json_loose

KB_SCHEMA = """
CREATE TABLE IF NOT EXISTS kb_topics (
    topic_id           TEXT PRIMARY KEY,
    normalized_topic   TEXT NOT NULL,
    intent             TEXT NOT NULL,
    summary_md         TEXT NOT NULL,
    bridges_json       TEXT NOT NULL DEFAULT '[]',
    papers_json        TEXT NOT NULL DEFAULT '[]',
    created_at         TEXT NOT NULL,
    last_used_at       TEXT NOT NULL,
    hit_count          INTEGER NOT NULL DEFAULT 0,
    cost_usd           REAL NOT NULL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_kb_topics_intent ON kb_topics(intent);
CREATE INDEX IF NOT EXISTS idx_kb_topics_last_used ON kb_topics(last_used_at);

CREATE TABLE IF NOT EXISTS kb_papers (
    bib_key            TEXT PRIMARY KEY,
    title              TEXT,
    year               INTEGER,
    venue              TEXT,
    url                TEXT,
    bibtex             TEXT,
    first_seen         TEXT NOT NULL,
    seen_in_topics     TEXT NOT NULL DEFAULT '[]'
);
"""

VALID_INTENTS = ("background", "open-questions", "cross-domain-bridge")
DEFAULT_TTL_DAYS = 30
MAX_WEB_SEARCHES = 10
# Claude web search is billed separately from tokens at $10 / 1,000 searches.
# Keep this visible beside the server-tool accounting so pricing drift is easy
# to update.
WEB_SEARCH_COST_USD = 0.01

# The custom tool definition exposed to other agents (Researcher, Coder, ...).
# The handler is `librarian.query` via `run_with_lit_review_tool` below.
LIT_REVIEW_TOOL = {
    "name": "lit_review",
    "description": (
        "Search the literature on a topic. Returns a synthesized summary, "
        "cross-domain bridges, and bib_keys you can cite. Cached aggressively "
        "in lab/knowledge/kb.sqlite — call freely when entering a new "
        "conceptual area. Reuse topic_ids from the 'kb cache index' block "
        "when possible (those are free)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": (
                    "Concise topic phrase, e.g. 'contrastive pretraining for "
                    "tabular data' or 'rotary positional embeddings'."
                ),
            },
            "intent": {
                "type": "string",
                "enum": list(VALID_INTENTS),
                "description": (
                    "background = canonical body of work; open-questions = "
                    "unresolved debates; cross-domain-bridge = connection "
                    "between two sub-fields you want to combine."
                ),
            },
            "force_refresh": {
                "type": "boolean",
                "description": "If true, bypass cache and re-search. Use sparingly.",
            },
        },
        "required": ["topic", "intent"],
    },
}


@dataclass
class LitResult:
    topic_id: str
    summary_md: str
    bridges: list[dict[str, Any]] = field(default_factory=list)
    papers: list[dict[str, Any]] = field(default_factory=list)
    from_cache: bool = False

    def summary_for_model(self) -> dict[str, Any]:
        """Compact form returned as a tool_result. Drops bibtex/urls (the
        model doesn't need them to reason); keeps bib_keys for citation."""
        return {
            "topic_id": self.topic_id,
            "from_cache": self.from_cache,
            "summary_md": self.summary_md,
            "bridges": self.bridges,
            "bib_keys": [p["bib_key"] for p in self.papers if p.get("bib_key")],
        }


def init_kb(kb_db: Path) -> None:
    kb_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(kb_db)
    try:
        conn.executescript(KB_SCHEMA)
        conn.commit()
    finally:
        conn.close()


_WS_RE = re.compile(r"\s+")


def normalize_topic(topic: str) -> str:
    t = topic.strip().lower()
    t = _WS_RE.sub(" ", t)
    return t.rstrip(".?!,;:")


def make_topic_id(normalized: str, intent: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", normalized).strip("_")
    return f"{slug}/{intent}"


def _lookup(kb_db: Path, topic_id: str, *, ttl_days: int, force: bool) -> LitResult | None:
    if force or not kb_db.exists():
        return None
    conn = sqlite3.connect(kb_db)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT * FROM kb_topics WHERE topic_id = ?",
            (topic_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    try:
        created = datetime.fromisoformat(row["created_at"])
    except ValueError:
        return None
    if datetime.now(timezone.utc) - created > timedelta(days=ttl_days):
        return None
    return LitResult(
        topic_id=row["topic_id"],
        summary_md=row["summary_md"],
        bridges=json.loads(row["bridges_json"] or "[]"),
        papers=json.loads(row["papers_json"] or "[]"),
        from_cache=True,
    )


def _bump_hit_count(kb_db: Path, topic_id: str) -> None:
    conn = sqlite3.connect(kb_db)
    try:
        conn.execute(
            "UPDATE kb_topics SET hit_count = hit_count + 1, last_used_at = ? WHERE topic_id = ?",
            (datetime.now(timezone.utc).isoformat(), topic_id),
        )
        conn.commit()
    finally:
        conn.close()


def _persist(
    kb_db: Path,
    *,
    topic_id: str,
    normalized: str,
    intent: str,
    result: dict[str, Any],
    cost_usd: float,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(kb_db)
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO kb_topics
                (topic_id, normalized_topic, intent, summary_md, bridges_json,
                 papers_json, created_at, last_used_at, hit_count, cost_usd)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """,
            (
                topic_id, normalized, intent,
                result.get("summary_md", ""),
                json.dumps(result.get("bridges", [])),
                json.dumps(result.get("papers", [])),
                now, now, cost_usd,
            ),
        )
        for paper in result.get("papers", []):
            bib_key = paper.get("bib_key")
            if not bib_key:
                continue
            existing = conn.execute(
                "SELECT seen_in_topics FROM kb_papers WHERE bib_key = ?",
                (bib_key,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO kb_papers
                        (bib_key, title, year, venue, url, bibtex,
                         first_seen, seen_in_topics)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        bib_key, paper.get("title"), paper.get("year"),
                        paper.get("venue"), paper.get("url"),
                        paper.get("bibtex", ""), now,
                        json.dumps([topic_id]),
                    ),
                )
            else:
                seen = json.loads(existing[0] or "[]")
                if topic_id not in seen:
                    seen.append(topic_id)
                    conn.execute(
                        "UPDATE kb_papers SET seen_in_topics = ? WHERE bib_key = ?",
                        (json.dumps(seen), bib_key),
                    )
        conn.commit()
    finally:
        conn.close()


def index_for_prompt(kb_db: Path, n: int = 40) -> str:
    """Markdown summary of the most-recently-used topics, for prompt injection.

    Goes inside the Researcher's static-cached block so the model can see what
    topic_ids already exist and reuse them rather than re-querying.
    """
    if not kb_db.exists():
        return "(empty)"
    conn = sqlite3.connect(kb_db)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT topic_id, last_used_at, papers_json
               FROM kb_topics ORDER BY last_used_at DESC LIMIT ?""",
            (n,),
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return "(empty)"
    lines = []
    for r in rows:
        try:
            papers = json.loads(r["papers_json"] or "[]")
        except json.JSONDecodeError:
            papers = []
        keys = ", ".join(p.get("bib_key", "") for p in papers[:6] if p.get("bib_key"))
        date = (r["last_used_at"] or "")[:10]
        lines.append(f"- `{r['topic_id']}` ({date}) — {keys or '(no bib_keys)'}")
    return "\n".join(lines)


def _build_messages(*, topic: str, intent: str) -> list[dict[str, Any]]:
    static_block = (
        "## Bib-key convention\n\n"
        "Every paper you cite MUST get a stable BibTeX-style key:\n"
        "  `<firstauthorlastname><year><firstkeyword>` — all lowercase, no\n"
        "  separators. Examples: `havlicek2019supervised`, `hang2024minsnr`.\n"
        "Reuse keys across queries — same paper, same key.\n"
    )
    dynamic_block = (
        f"## Topic\n\n**topic**: {topic}\n\n**intent**: {intent}\n\n"
        "Use web_search aggressively (you have up to 10 searches). Prefer\n"
        "arxiv / OpenReview / Semantic Scholar URLs. Synthesize across the\n"
        "results; identify cross-domain bridges. Output the structured JSON\n"
        "documented in your system prompt — no prose, no fences."
    )
    return [{
        "role": "user",
        "content": [
            {"type": "text", "text": static_block, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": dynamic_block},
        ],
    }]


def _call_llm(
    *,
    topic: str,
    intent: str,
    client: anthropic.Anthropic,
    budget: BudgetTracker,
    model: str | None,
    max_tokens: int = 4096,
) -> tuple[dict[str, Any], float, str]:
    """Returns (parsed_json, cost_usd, raw_text)."""
    system_prompt = load_prompt("librarian")
    chosen = model or model_for("librarian")
    if chosen is None:
        raise RuntimeError("No model configured for Librarian")
    messages = _build_messages(topic=topic, intent=intent)
    tools = [{"type": "web_search_20250305", "name": "web_search",
              "max_uses": MAX_WEB_SEARCHES}]
    try:
        resp = client.messages.create(
            model=chosen,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system_prompt,
                     "cache_control": {"type": "ephemeral"}}],
            messages=messages,
            tools=tools,
        )
    except anthropic.BadRequestError:
        # web_search not available — fall back to a no-tools call.
        resp = client.messages.create(
            model=chosen,
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system_prompt,
                     "cache_control": {"type": "ephemeral"}}],
            messages=messages,
        )
    usage = CallUsage(
        input_tokens=resp.usage.input_tokens,
        output_tokens=resp.usage.output_tokens,
        cache_creation_input_tokens=getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
        cache_read_input_tokens=getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
    )
    server_searches = getattr(resp.usage, "server_tool_use", None)
    n_searches = (server_searches.web_search_requests if server_searches else 0) or 0
    record = budget.record(
        agent="librarian", model=billing_model(client, chosen), usage=usage,
        extra_cost_usd=n_searches * WEB_SEARCH_COST_USD,
        notes=f"topic={topic[:60]} | searches={n_searches}",
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
    try:
        parsed = parse_json_loose(text, must_contain='"summary_md"')
    except json.JSONDecodeError:
        parsed = {
            "summary_md": "(no parseable JSON; raw text saved in librarian_log)",
            "bridges": [],
            "papers": [],
        }
    return parsed, float(record.get("cost_usd", 0.0)), text


def query(
    *,
    topic: str,
    intent: str,
    paths: LabPaths,
    budget: BudgetTracker,
    client: anthropic.Anthropic | None,
    force: bool = False,
    ttl_days: int = DEFAULT_TTL_DAYS,
    model: str | None = None,
) -> LitResult:
    """Cache-first lit review. On miss, call Anthropic with web_search.

    Idempotent on the cache: repeat calls for the same (topic, intent) within
    the TTL return the cached row and bump hit_count.
    """
    if intent not in VALID_INTENTS:
        raise ValueError(
            f"intent must be one of {VALID_INTENTS}; got {intent!r}"
        )
    init_kb(paths.kb_db)
    normalized = normalize_topic(topic)
    topic_id = make_topic_id(normalized, intent)

    cached = _lookup(paths.kb_db, topic_id, ttl_days=ttl_days, force=force)
    if cached is not None:
        _bump_hit_count(paths.kb_db, topic_id)
        append_jsonl(paths.librarian_log, {
            "ts": datetime.now(timezone.utc).isoformat(),
            "topic_id": topic_id, "topic": topic, "intent": intent,
            "from_cache": True, "cost_usd": 0.0,
        })
        return cached

    if client is None:
        # Dry-run / offline mode — return a placeholder; persist briefly so
        # repeat calls don't churn.
        result = {
            "summary_md": f"(offline placeholder for topic={topic!r}, intent={intent!r})",
            "bridges": [],
            "papers": [],
        }
        _persist(paths.kb_db, topic_id=topic_id, normalized=normalized,
                 intent=intent, result=result, cost_usd=0.0)
        append_jsonl(paths.librarian_log, {
            "ts": datetime.now(timezone.utc).isoformat(),
            "topic_id": topic_id, "topic": topic, "intent": intent,
            "from_cache": False, "offline": True, "cost_usd": 0.0,
        })
        return LitResult(topic_id=topic_id, summary_md=result["summary_md"],
                         bridges=[], papers=[], from_cache=False)

    t0 = time.monotonic()
    parsed, cost_usd, raw_text = _call_llm(
        topic=topic, intent=intent, client=client, budget=budget, model=model,
    )
    duration = time.monotonic() - t0

    _persist(paths.kb_db, topic_id=topic_id, normalized=normalized,
             intent=intent, result=parsed, cost_usd=cost_usd)
    append_jsonl(paths.librarian_log, {
        "ts": datetime.now(timezone.utc).isoformat(),
        "topic_id": topic_id, "topic": topic, "intent": intent,
        "from_cache": False, "cost_usd": cost_usd,
        "duration_seconds": duration,
        "n_papers": len(parsed.get("papers", [])),
        "n_bridges": len(parsed.get("bridges", [])),
        "raw_response_chars": len(raw_text),
    })
    return LitResult(
        topic_id=topic_id,
        summary_md=parsed.get("summary_md", ""),
        bridges=parsed.get("bridges", []),
        papers=parsed.get("papers", []),
        from_cache=False,
    )


def run_with_lit_review_tool(
    *,
    client: anthropic.Anthropic,
    system_prompt: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    model: str,
    paths: LabPaths,
    budget: BudgetTracker,
    agent: str,
    extra_tools: list[dict[str, Any]] | None = None,
    max_tokens: int = 4096,
    max_lit_calls: int = 5,
) -> tuple[str, list[str]]:
    """Drive a messages.create loop with the lit_review tool wired in.

    Other agents (Researcher, Coder) call this with their own system prompt
    and message history. Returns (final_assistant_text, consulted_topic_ids).
    On overflow, returns an error tool_result so the model emits its final
    answer instead of looping further.
    """
    consulted: list[str] = []
    n_lit_calls = 0
    tools = [LIT_REVIEW_TOOL] + list(extra_tools or [])
    final_text = ""

    while True:
        resp = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=messages,
            tools=tools,
        )
        usage = CallUsage(
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
            cache_creation_input_tokens=getattr(resp.usage, "cache_creation_input_tokens", 0) or 0,
            cache_read_input_tokens=getattr(resp.usage, "cache_read_input_tokens", 0) or 0,
        )
        budget.record(
            agent=agent, model=billing_model(client, model), usage=usage,
            notes=f"turn {n_lit_calls} | stop={resp.stop_reason}",
        )

        if resp.stop_reason != "tool_use":
            final_text = "".join(
                b.text for b in resp.content if getattr(b, "type", "") == "text"
            )
            break

        tool_results: list[dict[str, Any]] = []
        for block in resp.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            if block.name != "lit_review":
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps({"error": f"unknown tool {block.name}"}),
                    "is_error": True,
                })
                continue
            n_lit_calls += 1
            if n_lit_calls > max_lit_calls:
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps({
                        "error": (
                            f"max lit_review calls ({max_lit_calls}) per pass "
                            "exceeded; produce final answer now"
                        ),
                    }),
                    "is_error": True,
                })
                continue
            inp = block.input or {}
            try:
                lit = query(
                    topic=inp["topic"],
                    intent=inp["intent"],
                    paths=paths,
                    budget=budget,
                    client=client,
                    force=bool(inp.get("force_refresh", False)),
                )
                consulted.append(lit.topic_id)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(lit.summary_for_model()),
                })
            except Exception as e:
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps({"error": f"{type(e).__name__}: {e}"}),
                    "is_error": True,
                })

        messages.append({"role": "assistant", "content": resp.content})
        messages.append({"role": "user", "content": tool_results})

    return final_text, consulted
