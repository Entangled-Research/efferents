"""Spend tracking, daily cap, model routing.

Pricing as of 2026-09 (per million tokens):

    claude-opus-4-7    : $5 in, $25 out
    claude-sonnet-4-6  : $3 in, $15 out
    claude-haiku-4-5   : $1 in, $5 out
    claude-sonnet-5    : $2 in, $10 out
    zai/glm-5.3        : $1.40 in, $4.40 out, $0.26 cache reads

Cache pricing (relative to input):
    cache_creation_input_tokens : 1.25x base input
    cache_read_input_tokens     : 0.1x base input

These constants are baked in. Update when provider prices change.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from efferents.agents.state import append_jsonl, read_jsonl

PRICING_PER_MTOK = {
    "claude-sonnet-5":   {"input": 2.00, "output": 10.00},
    # Z.ai general API, verified 2026-09-11. Explicit pricing keeps new GLM
    # models budgeted even before LiteLLM's catalogue contains them.
    "zai/glm-5.3":      {"input": 1.40, "output": 4.40, "cache_read": 0.26},
    "claude-opus-4-7":    {"input":  5.00, "output": 25.00},
    "claude-sonnet-4-6":  {"input":  3.00, "output": 15.00},
    "claude-haiku-4-5":   {"input":  1.00, "output":  5.00},
}

CACHE_WRITE_MULT = 1.25
CACHE_READ_MULT = 0.10

# Default model routing per agent role.
ROLE_MODEL = {
    "researcher": "claude-sonnet-4-6",  # legacy fallback; dialogue uses student/supervisor
    "student":    "claude-sonnet-4-6",
    "supervisor": "claude-sonnet-4-6",  # may escalate to Opus via model_for_supervisor()
    "executor":   None,   # Executor doesn't call Anthropic
    "analyst":    "claude-opus-4-7",
    "writer":     "claude-sonnet-4-6",
    "coder":      "claude-opus-4-7",  # architectural code edits need careful reasoning
    "librarian":  "claude-sonnet-4-6",  # synthesis + web_search; not Opus-grade reasoning
    "reviewer":   "claude-sonnet-4-6",  # 3x per submission; one critical/neutral/enthusiast
    "rebuttal":   "claude-sonnet-4-6",  # 1x per submission; student-voiced reply
}

SUPERVISOR_OPUS_STREAK_THRESHOLD = 2


@dataclass(frozen=True)
class CallUsage:
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


def cost_usd(model: str, usage: CallUsage) -> float:
    if "," in model:
        # Estimates without a response use the preferred entry. Agent call
        # sites use billing_model() to record the actual model after fallback.
        model = model.split(",", 1)[0].strip()
    p = PRICING_PER_MTOK.get(model)
    if p is None:
        # LiteLLM maintains pricing for its provider catalogue.  Keep the
        # framework's small Claude table as the stable default, then consult
        # that catalogue for provider-qualified models.
        try:
            import litellm
            candidates = (model, model.split("/", 1)[-1])
            entry = next((litellm.model_cost.get(name) for name in candidates
                          if litellm.model_cost.get(name)), None)
        except (ImportError, AttributeError):
            entry = None
        if entry is None:
            return 0.0
        return (
            usage.input_tokens * float(entry.get("input_cost_per_token", 0.0) or 0.0)
            + usage.output_tokens * float(entry.get("output_cost_per_token", 0.0) or 0.0)
            + usage.cache_read_input_tokens * float(entry.get(
                "cache_read_input_token_cost", entry.get("input_cost_per_token", 0.0)
            ) or 0.0)
        )
    base_in = p["input"] / 1_000_000
    base_out = p["output"] / 1_000_000
    return (
        usage.input_tokens * base_in
        + usage.output_tokens * base_out
        + usage.cache_creation_input_tokens * base_in * CACHE_WRITE_MULT
        + usage.cache_read_input_tokens * p.get("cache_read", p["input"] * CACHE_READ_MULT) / 1_000_000
    )


def billing_model(client: Any, requested: str) -> str:
    """Record the actual provider after a successful routed call."""
    served = getattr(client, "last_served_model", None)
    if isinstance(served, str) and served in [part.strip() for part in requested.split(",")]:
        return served
    return requested


def estimate_call_cost_usd(model: str, max_tokens: int, input_estimate: int = 0) -> float:
    """Worst-case cost of one call: every input token billed at the cache-write
    rate and the full ``max_tokens`` output budget consumed.  Used by
    ``BudgetTracker.reserve`` so a cap is enforced *before* money is spent."""
    base = cost_usd(model, CallUsage(input_tokens=input_estimate, output_tokens=max_tokens))
    input_only = cost_usd(model, CallUsage(input_tokens=input_estimate, output_tokens=0))
    return base + input_only * (CACHE_WRITE_MULT - 1.0)


def utc_date_str(ts: str | None = None) -> str:
    dt = datetime.fromisoformat(ts) if ts else datetime.now(timezone.utc)
    return dt.strftime("%Y-%m-%d")


class BudgetExhausted(RuntimeError):
    """Raised by ``BudgetTracker.reserve`` when the next call would breach a cap.

    ``scope`` is ``"daily"`` or ``"total"``; ``spend``/``cap``/``estimate`` are
    USD so callers can log an auditable line without re-reading the ledger.
    """

    def __init__(self, scope: str, *, spend: float, cap: float, estimate: float):
        self.scope = scope
        self.spend = spend
        self.cap = cap
        self.estimate = estimate
        super().__init__(
            f"{scope} cap reached: spent ${spend:.2f} of ${cap:.2f}; "
            f"next call could cost up to ${estimate:.4f}"
        )


class BudgetTracker:
    """Append-only estimated spend ledger with a hard pre-call cap.

    ``should_pause`` is the coarse loop-level check; ``reserve`` is the hard
    check the model client runs before every request, so spend can overshoot
    a cap by at most one call's actual cost (and only when that call's true
    cost exceeds its worst-case estimate).
    """

    def __init__(
        self,
        ledger_path: Path,
        daily_cap_usd: float = 100.0,
        total_cap_usd: float | None = None,
    ):
        self.path = ledger_path
        self.daily_cap = daily_cap_usd
        self.total_cap = total_cap_usd

    def record(
        self,
        *,
        agent: str,
        model: str,
        usage: CallUsage,
        extra_cost_usd: float = 0.0,
        cache_hit_rate: float | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        ts = datetime.now(timezone.utc).isoformat()
        token_cost = cost_usd(model, usage)
        record = {
            "ts": ts,
            "agent": agent,
            "model": model,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_creation_input_tokens": usage.cache_creation_input_tokens,
            "cache_read_input_tokens": usage.cache_read_input_tokens,
            "token_cost_usd": token_cost,
            "extra_cost_usd": extra_cost_usd,
            "cost_usd": token_cost + extra_cost_usd,
            "cache_hit_rate": cache_hit_rate,
            "notes": notes,
        }
        append_jsonl(self.path, record)
        return record

    def spend_today(self, today: str | None = None) -> float:
        today = today or utc_date_str()
        return sum(
            r["cost_usd"]
            for r in read_jsonl(self.path)
            if r.get("ts", "").startswith(today)
        )

    def spend_total(self) -> float:
        return sum(r.get("cost_usd", 0.0) for r in read_jsonl(self.path))

    def cache_stats(self, n_recent: int = 50) -> dict[str, float]:
        recs = read_jsonl(self.path)[-n_recent:]
        creates = sum(r.get("cache_creation_input_tokens", 0) or 0 for r in recs)
        reads = sum(r.get("cache_read_input_tokens", 0) or 0 for r in recs)
        ins = sum(r.get("input_tokens", 0) or 0 for r in recs)
        denom = max(1, ins + creates + reads)
        return {
            "n_calls": len(recs),
            "cache_read_share": reads / denom,
            "cache_create_share": creates / denom,
            "fresh_input_share": ins / denom,
        }

    def should_pause(self) -> bool:
        if self.spend_today() >= self.daily_cap:
            return True
        return self.total_cap is not None and self.spend_total() >= self.total_cap

    def reserve(self, model: str, max_tokens: int | None, input_estimate: int = 0) -> float:
        """Refuse the next call unless its worst-case cost fits under every cap.

        Returns the estimate (USD) on success; raises ``BudgetExhausted``
        otherwise.  A cap that is already met refuses even a zero-priced
        (unknown-model) call so an unpriced provider cannot bypass the cap.
        """
        estimate = estimate_call_cost_usd(model, int(max_tokens or 0), input_estimate)
        today = self.spend_today()
        if today >= self.daily_cap or today + estimate > self.daily_cap:
            raise BudgetExhausted("daily", spend=today, cap=self.daily_cap, estimate=estimate)
        if self.total_cap is not None:
            total = self.spend_total()
            if total >= self.total_cap or total + estimate > self.total_cap:
                raise BudgetExhausted("total", spend=total, cap=self.total_cap, estimate=estimate)
        return estimate


def model_for(role: str, override: str | None = None) -> str | None:
    if override:
        return override
    import os
    role_override = os.environ.get(f"EFFERENTS_MODEL_{role.upper()}")
    if role_override:
        return role_override
    default_override = os.environ.get("EFFERENTS_MODEL")
    if default_override:
        return default_override
    return ROLE_MODEL.get(role)


def model_for_supervisor(saturation_streak: int) -> str:
    """Supervisor escalates to Opus when the saturation score has been high
    for ``SUPERVISOR_OPUS_STREAK_THRESHOLD`` consecutive iterations — the loop
    is genuinely stuck and Sonnet's pivots aren't landing."""
    import os
    configured = os.environ.get("EFFERENTS_MODEL_SUPERVISOR") or os.environ.get("EFFERENTS_MODEL")
    if configured:
        return configured
    if saturation_streak >= SUPERVISOR_OPUS_STREAK_THRESHOLD:
        return "claude-opus-4-7"
    return ROLE_MODEL["supervisor"]
