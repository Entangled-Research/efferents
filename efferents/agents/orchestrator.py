"""The 24/7 loop. Researcher refills the queue; Executor drains it; Analyst writes
periodic digests and pushes notifications.

Restart-safe: all state lives in lab/. On startup, we just resume.

Stopping: send SIGTERM (or Ctrl-C interactively); the loop exits cleanly between
iterations. If killed mid-run, the current proposal is lost from the queue but
nothing is corrupted.
"""
from __future__ import annotations

import os
import signal
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import re as _re

from efferents.agents import analyst, coder, executor, researcher, writer
from efferents.agents import notebook as _notebook
from efferents.agents.budget import BudgetExhausted, BudgetTracker, model_for
from efferents.agents.model_client import (
    classify_provider_error,
    make_client,
    probe_request,
)
from efferents.agents.notify import notify_all
from efferents.agents.state import (
    LabPaths,
    StudentStateView,
    campaign_close,
    campaign_open_list,
    campaign_stale_open,
    init_lab,
    lab_paths,
    load_state,
    notebook_append,
    queue_pop,
    queue_ack,
    queue_requeue_inflight,
    queue_push,
    queue_size,
    runs_count,
    save_state,
    now_iso,
)
from efferents import lab as _lab
from efferents import steer as _steer
from efferents.migrations.runner import apply_campaigns_migration

# The per-run notebook entry is rendered by the executor's ``_format_outcome``
# hook; swap in the compact renderer so notebook_tail() is not flooded by a
# table of every metric column after every run.
_notebook.install_compact_run_entries(executor)

_VALID_MODES = {"refine", "moonshot", "devils_advocate", "escape_to_code"}
_FORCE_MODE_RE = _re.compile(r"^force_mode:\s*(\S+)\s*$", _re.MULTILINE)

# Backoff schedule (seconds). Generic step failures start at one minute; a
# credit/auth halt starts at five. Both double up to the one-hour cap.
GENERIC_BACKOFF_START_S = 60.0
HALT_BACKOFF_START_S = 300.0
BACKOFF_CAP_S = 3600.0
# Owner notifications: at most one per distinct event per this interval.
NOTIFY_MIN_INTERVAL_S = 3600.0
DEFAULT_STALL_HOURS = 6.0


def _env_float(name: str, default: float | None) -> float | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def read_force_mode(context_dir: Path | str) -> str | None:
    """Return the LAST `force_mode: <name>` directive in research_log.md
    or None if absent / unreadable / name unknown."""
    log = Path(context_dir) / "research_log.md"
    if not log.exists():
        return None
    matches = _FORCE_MODE_RE.findall(log.read_text())
    if not matches:
        return None
    candidate = matches[-1].strip()
    return candidate if candidate in _VALID_MODES else None


def select_mode(state: dict, *, override: str | None) -> str:
    if override in _VALID_MODES:
        return override
    flat = int(state.get("digests_without_improvement", 0))
    if flat >= 4:
        return "escape_to_code"
    if flat >= 3:
        return "devils_advocate"
    if flat >= 2:
        return "moonshot"
    return "refine"


def close_stale_campaigns(db_path: Path, *, lab_id: str, hours: float = 48.0) -> list[str]:
    stale = campaign_stale_open(db_path, lab_id, hours=hours)
    ids = [c["id"] for c in stale]
    for cid in ids:
        campaign_close(db_path, cid, reason="stale")
    return ids


def _hours_since(iso_ts: str | None) -> float:
    if not iso_ts:
        return 1e9
    try:
        t = datetime.fromisoformat(iso_ts)
    except ValueError:
        return 1e9
    return (datetime.now(timezone.utc) - t).total_seconds() / 3600.0


def _seconds_until_next_utc_day() -> float:
    now = datetime.now(timezone.utc)
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    return (midnight - now).total_seconds()


def _lab_label() -> str:
    """Notification title prefix — the active lab_id, so a user running
    several labs gets distinct notifications. Falls back to 'efferents'."""
    try:
        return _lab.get_config().lab_id
    except Exception:
        return "efferents"


class Orchestrator:
    def __init__(
        self,
        *,
        lab_dir: str | Path = "lab",
        context_dir: str | Path = "context",
        daily_cap_usd: float = 10.0,
        runs_per_digest: int = 40,
        hours_per_digest: float = 4.0,
        runs_per_coder: int = 8,
        hours_per_coder: float = 6.0,
        runs_per_paper: int = 20,
        hours_per_paper: float = 6.0,
        dry_run: bool = False,
        startup_message: str | None = None,
        total_cap_usd: float | None = None,
        stall_hours: float | None = None,
    ):
        self.paths: LabPaths = lab_paths(lab_dir)
        init_lab(self.paths)
        apply_campaigns_migration(self.paths.runs_db)
        self.context_dir = Path(context_dir)
        # ``total_cap_usd`` is a lifetime cap on the ledger. The CLI passes
        # LabConfig.budget.total_cap_usd; the env var is the fallback when the
        # caller leaves it unset.
        if total_cap_usd is None:
            total_cap_usd = _env_float("EFFERENTS_TOTAL_CAP_USD", None)
        self.budget = BudgetTracker(
            self.paths.budget, daily_cap_usd=daily_cap_usd, total_cap_usd=total_cap_usd
        )
        self.runs_per_digest = runs_per_digest
        self.hours_per_digest = hours_per_digest
        self.runs_per_coder = runs_per_coder
        self.hours_per_coder = hours_per_coder
        self.runs_per_paper = runs_per_paper
        self.hours_per_paper = hours_per_paper
        self.dry_run = dry_run
        self.stall_hours = (
            stall_hours if stall_hours is not None
            else _env_float("EFFERENTS_STALL_HOURS", DEFAULT_STALL_HOURS)
        )
        self._stop = False
        self._started_ts = now_iso()
        # event key -> monotonic time of the last owner notification.
        self._notified_at: dict[str, float] = {}
        # Set to True after a successful Coder commit. The wrapper script
        # observes the special exit code and re-spawns the process so
        # the lab's modules are reloaded fresh from disk.
        self.restart_requested = False

        self.client: Any | None = None
        if not dry_run:
            self.client = make_client(budget=self.budget)

        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

        if startup_message:
            notebook_append(self.paths.notebook, f"## {now_iso()} — orchestrator start\n\n{startup_message}\n")
            # Suppress the "started" push when we're respawning right after a
            # Coder commit (the user just got the "code committed" push 2s ago;
            # they don't need a "started" follow-up).
            state = load_state(self.paths.state)
            last_coder = state.get("last_coder_ts")
            if not last_coder or _hours_since(last_coder) > 0.05:  # >3 minutes
                notify_all(title=f"{_lab_label()} started", message=startup_message)

    def _handle_signal(self, signum: int, _frame: Any) -> None:
        self._stop = True
        notebook_append(self.paths.notebook, f"## {now_iso()} — received signal {signum}, stopping\n")

    def _next_student_id(self) -> str:
        """Round-robin: pick the next student to work on, advancing the cursor.

        Lives in state.json under `_global.current_student_idx` so it
        survives orchestrator restarts. With STUDENTS=[primary] (default),
        this always returns 'primary' and behavior matches single-student.
        """
        ids = _lab.student_ids()
        if not ids:
            return _lab.DEFAULT_STUDENT_ID
        if len(ids) == 1:
            return ids[0]
        state = load_state(self.paths.state)
        global_state = state.setdefault("_global", {})
        idx = int(global_state.get("current_student_idx", -1))
        idx = (idx + 1) % len(ids)
        global_state["current_student_idx"] = idx
        save_state(self.paths.state, state)
        return ids[idx]

    def _refill_queue(self) -> int:
        if queue_size(self.paths.queue) > 0:
            return 0
        if self.dry_run:
            # Hardcoded probe proposal so the loop is exercisable without API calls.
            opens = campaign_open_list(self.paths.runs_db, _lab.LAB_ID)
            campaign_id = opens[0]["id"] if opens else None
            queue_push(
                self.paths.queue,
                {
                    "name": f"dryrun_{datetime.now(timezone.utc).strftime('%H%M%S')}",
                    "hypothesis": "Dry-run probe: pipeline only.",
                    "expected": "metrics arbitrary",
                    "config_overrides": {"run.seed": 123},
                    "campaign_id": campaign_id,
                    "mode": "refine",
                    "student_id": _lab.DEFAULT_STUDENT_ID,
                    "use_smoke_command": True,
                },
            )
            return 1
        if self.budget.should_pause():
            self._handle_budget_exhausted(self._budget_exhaustion())
            return 0
        # Skip Researcher only on FRESH Coder backlog (≤2h since last
        # Researcher call). Original concern: saturation-driven rounds emit
        # architectural-only output and re-calling Researcher right after
        # spins lit_review at ~$2/iter. But an accumulating backlog (Coder
        # drains 1/6h, Researcher emits N/call) would permanently starve the
        # executor without this time bound.
        state = load_state(self.paths.state)
        # Choose which student gets this Researcher pass (round-robin across
        # _lab.STUDENTS). With one student configured, picks 'primary' every
        # time and behavior matches single-student.
        student_id = self._next_student_id()
        sstate = StudentStateView(state, student_id)
        if (
            _hours_since(sstate.get("last_researcher_ts")) < 2.0
            and coder.select_pending_proposal(paths=self.paths, student_id=student_id) is not None
        ):
            return 0
        override = read_force_mode(self.context_dir)
        mode = select_mode(state, override=override)
        notebook_append(
            self.paths.notebook,
            f"## {now_iso()} — Researcher mode: {mode} student={student_id} "
            f"(flat_digests={state.get('digests_without_improvement', 0)}, override={override})\n"
        )
        result = researcher.propose(
            paths=self.paths,
            context_dir=self.context_dir,
            budget=self.budget,
            client=self.client,
            mode=mode,
            student_id=student_id,
        )
        proposals = result.get("proposals", [])
        if not proposals:
            err = result.get("error")
            notebook_append(
                self.paths.notebook,
                f"## {now_iso()} — Researcher returned no proposals. Error: {err}\n\n"
                f"Raw: ```{result.get('raw', '')[:500]}```\n",
            )
            return 0
        for p in proposals:
            queue_push(self.paths.queue, p)
        return len(proposals)

    def _maybe_digest(self) -> None:
        state = load_state(self.paths.state)
        n_runs = runs_count(self.paths.runs_db)
        last_runs = int(state.get("last_digest_runs", 0))
        last_ts = state.get("last_digest_ts")
        runs_since = n_runs - last_runs
        hours_since = _hours_since(last_ts)
        if runs_since < self.runs_per_digest and hours_since < self.hours_per_digest:
            return

        if self.dry_run:
            notebook_append(self.paths.notebook, f"## {now_iso()} — digest skipped (dry-run)\n")
            state["last_digest_runs"] = n_runs
            state["last_digest_ts"] = now_iso()
            save_state(self.paths.state, state)
            return

        try:
            res = analyst.write_digest(
                paths=self.paths,
                context_dir=self.context_dir,
                budget=self.budget,
                client=self.client,
            )
            state["last_digest_runs"] = n_runs
            state["last_digest_ts"] = now_iso()
            state["last_digest_path"] = res["path"]
            save_state(self.paths.state, state)
        except Exception as e:
            notebook_append(
                self.paths.notebook, f"## {now_iso()} — digest FAILED: {type(e).__name__}: {e}\n"
            )

    # --- owner-facing governance: halts, notifications, stall detection ------

    def _notify_event(
        self, event: str, title: str, message: str, *, priority: int = 3, sound: bool = False,
    ) -> bool:
        """Owner notification rate-limited to one per ``event`` per hour.

        Returns True when a notification was actually fired so callers can
        pair it with a single notebook line instead of one per retry.
        """
        now = time.monotonic()
        last = self._notified_at.get(event)
        if last is not None and now - last < NOTIFY_MIN_INTERVAL_S:
            return False
        self._notified_at[event] = now
        notify_all(
            title=f"{_lab_label()}: {title}", message=message,
            priority=priority, sound=sound, lab_id=_lab_label(),
        )
        return True

    def _halt(self, kind: str, reason: str) -> None:
        """Pause the lab in an auditable way: halt_reason.txt, state.json,
        notebook entry, and one high-priority owner notification."""
        text = f"{kind}: {reason}"
        (self.paths.root / "halt_reason.txt").write_text(text + "\n")
        state = load_state(self.paths.state)
        state["status"] = "paused"
        state["halt_reason"] = text
        state["halted_at"] = now_iso()
        save_state(self.paths.state, state)
        notebook_append(self.paths.notebook, f"## {now_iso()} — HALT ({kind}): {reason}\n")
        self._notify_event(f"halt:{kind}", f"halted ({kind})", reason, priority=5, sound=True)

    def _resume(self, note: str) -> None:
        halt = self.paths.root / "halt_reason.txt"
        if halt.exists():
            halt.unlink()
        state = load_state(self.paths.state)
        state["status"] = "running"
        state.pop("halt_reason", None)
        state["resumed_at"] = now_iso()
        save_state(self.paths.state, state)
        notebook_append(self.paths.notebook, f"## {now_iso()} — resumed: {note}\n")

    def _interruptible_sleep(self, secs: float) -> None:
        """Sleep in 60-second chunks so SIGTERM/SIGINT can interrupt."""
        end = time.monotonic() + secs
        while not self._stop and time.monotonic() < end:
            time.sleep(min(60, max(1.0, end - time.monotonic())))

    def _probe_provider(self) -> None:
        """Cheapest possible request; raises exactly what a real call would."""
        if self.client is None:
            return
        self.client.messages.create(**probe_request(model_for("student")))

    def _wait_for_provider(self, kind: str) -> bool:
        """Exponential backoff (5 min -> 1 h cap) re-probing the provider.

        Returns True once a probe succeeds, False if stopped while waiting.
        The researcher loop is *not* run in the meantime.
        """
        delay = HALT_BACKOFF_START_S
        while not self._stop:
            notebook_append(
                self.paths.notebook,
                f"## {now_iso()} — halted ({kind}); re-probing provider in {delay/60:.0f} min\n",
            )
            self._interruptible_sleep(delay)
            if self._stop:
                return False
            try:
                self._probe_provider()
                return True
            except BudgetExhausted:
                # Budget, not the provider, is the binding constraint now; let
                # the main loop handle it via the daily/total cap path.
                return True
            except Exception as e:  # provider still failing; keep backing off
                probe_kind, _ = classify_provider_error(e)
                notebook_append(
                    self.paths.notebook,
                    f"## {now_iso()} — probe failed ({probe_kind}): {type(e).__name__}: {e}\n",
                )
                delay = min(delay * 2, BACKOFF_CAP_S)
        return False

    def _check_stall(self) -> None:
        """Notify the owner when no run has succeeded for ``stall_hours``."""
        if not self.stall_hours or self.stall_hours <= 0:
            return
        state = load_state(self.paths.state)
        anchor = state.get("last_success_ts") or self._started_ts
        idle = _hours_since(anchor)
        if idle < self.stall_hours:
            return
        message = f"no successful run for {idle:.1f}h (threshold {self.stall_hours:g}h)"
        if self._notify_event("stall", "stalled", message, priority=4):
            notebook_append(self.paths.notebook, f"## {now_iso()} — STALL: {message}\n")

    def _budget_exhaustion(self) -> BudgetExhausted:
        """Describe which cap ``should_pause`` tripped, for the halt record."""
        total_cap = self.budget.total_cap
        if total_cap is not None and self.budget.spend_total() >= total_cap:
            return BudgetExhausted(
                "total", spend=self.budget.spend_total(), cap=total_cap, estimate=0.0
            )
        return BudgetExhausted(
            "daily", spend=self.budget.spend_today(), cap=self.budget.daily_cap, estimate=0.0
        )

    def _handle_budget_exhausted(self, e: BudgetExhausted) -> None:
        if e.scope == "total":
            # A lifetime cap never frees on its own; stop cleanly and leave
            # halt_reason.txt for `efferents status` and the funder.
            self._halt("budget", str(e))
            self._stop = True
            return
        self._halt("budget", str(e))
        self._sleep_paused(str(e), notify=False)
        if not self._stop:
            self._resume("new UTC day; daily cap reset")

    def _sleep_paused(self, reason: str, *, notify: bool = True) -> None:
        secs = _seconds_until_next_utc_day()
        notebook_append(self.paths.notebook, f"## {now_iso()} — pausing: {reason}. Sleeping {secs/3600:.1f}h.\n")
        if notify:
            self._notify_event("paused", "paused", reason)
        self._interruptible_sleep(secs)

    def _maybe_code(self) -> None:
        if self.dry_run or self.client is None:
            return
        if not _lab.get_config().autonomy.coder_enabled:
            return
        state = load_state(self.paths.state)
        n_runs = runs_count(self.paths.runs_db)
        # Walk students in declaration order looking for one whose Coder is
        # due and has a pending backlog. With one student, this collapses
        # to the legacy behavior.
        for sid in _lab.student_ids():
            sstate = StudentStateView(state, sid)
            last_runs = int(sstate.get("last_coder_runs", 0))
            last_ts = sstate.get("last_coder_ts")
            if (n_runs - last_runs) < self.runs_per_coder and _hours_since(last_ts) < self.hours_per_coder:
                continue
            if self.budget.should_pause():
                return
            proposal = coder.select_pending_proposal(paths=self.paths, student_id=sid)
            if proposal is None:
                # Bump cursor anyway so we don't recheck this student every iteration.
                sstate["last_coder_runs"] = n_runs
                sstate["last_coder_ts"] = now_iso()
                save_state(self.paths.state, state)
                continue
            break
        else:
            # No student had pending work.
            return
        try:
            result = coder.implement_proposal(
                proposal=proposal,
                paths=self.paths,
                budget=self.budget,
                client=self.client,
            )
            sstate["last_coder_runs"] = n_runs
            sstate["last_coder_ts"] = now_iso()
            save_state(self.paths.state, state)
            if result.ok:
                notify_all(
                    title=f"{_lab_label()}: code committed",
                    message=f"{result.name}: {result.summary or ''} ({result.commit_sha})",
                )
                notebook_append(
                    self.paths.notebook,
                    f"## {now_iso()} — Coder committed; subsequent subprocess runs "
                    "will load the new source\n",
                )
            elif result.patch_path:
                # Review mode: the Coder already wrote the notebook line; the
                # owner gets one notification per hour that a patch is waiting.
                self._notify_event(
                    "patch_review", "patch awaits review",
                    f"{result.name}: {result.patch_path}",
                )
        except Exception as e:
            notebook_append(
                self.paths.notebook,
                f"## {now_iso()} — Coder step FAILED: {type(e).__name__}: {e}\n",
            )

    def _maybe_write(self) -> None:
        if self.dry_run or self.client is None:
            return
        state = load_state(self.paths.state)
        n_runs = runs_count(self.paths.runs_db)
        last_runs = int(state.get("last_paper_runs", 0))
        last_ts = state.get("last_paper_ts")
        if (n_runs - last_runs) < self.runs_per_paper and _hours_since(last_ts) < self.hours_per_paper:
            return
        if self.budget.should_pause():
            return  # do NOT bump the cursor; retry when budget frees

        lab_root = self.paths.runs_db.parent
        wpaths = writer.writer_paths(
            lab=lab_root,
            paper=lab_root / "paper",
            reports=lab_root / "reports",
            context=self.context_dir,
        )
        for campaign in campaign_open_list(self.paths.runs_db, _lab.LAB_ID):
            cid = campaign["id"]
            if (wpaths.paper / f"{cid}.md").exists():
                continue  # one paper per campaign; no re-write / re-review
            try:
                artifact = writer.write_phase_a_paper(
                    wpaths,
                    campaign,
                    client=self.client,
                    budget=self.budget,
                    gain_threshold=_lab.PEER_REVIEW_GAIN_THRESHOLD,
                )
                if artifact is not None:
                    notify_all(
                        title=f"{_lab_label()}: paper composed",
                        message=f"campaign {cid}",
                    )
                    notebook_append(
                        self.paths.notebook,
                        f"## {now_iso()} — Writer composed a paper for {cid}\n",
                    )
            except Exception as e:
                notebook_append(
                    self.paths.notebook,
                    f"## {now_iso()} — Writer step FAILED for {cid}: {type(e).__name__}: {e}\n",
                )

        state["last_paper_runs"] = n_runs
        state["last_paper_ts"] = now_iso()
        save_state(self.paths.state, state)

    def step(self) -> dict[str, Any]:
        """One iteration. Returns telemetry dict.

        Coder + digest cadence are checked every step, even when the queue is
        empty — otherwise architectural-only Researcher outputs (saturation-aware
        pivot) starve the loop: the executor has nothing to run, and the Coder
        never gets called to drain proposed_changes.md.
        """
        if _steer.step_hook(self):  # owner steering; True while paused by owner
            return {"event": "owner_paused", "added": 0}
        from efferents.agents.routing import refresh_students
        refresh_students(self.paths.root)
        from efferents.agents.conference import attend
        attendance = attend(cfg=_lab.get_config(), lab_root=self.paths.root)
        if attendance is not None:
            notebook_append(
                self.paths.notebook,
                f"## {now_iso()} — conference visit {attendance['visit']}: "
                f"{len(attendance['received'])} talks received; "
                f"{len(attendance['errors'])} peer errors. "
                "See lab/conference/attendance.jsonl and inbox.jsonl.\n",
            )
        n_added = self._refill_queue()
        proposal = queue_pop(self.paths.queue)
        if proposal is None:
            # No config proposal to execute. Still fire digest + coder cadence;
            # if the Researcher just produced architectural proposals, the Coder
            # should pick them up rather than waiting for a run that won't come.
            self._maybe_digest()
            self._maybe_code()
            self._maybe_write()
            closed = close_stale_campaigns(self.paths.runs_db, lab_id=_lab.LAB_ID)
            if closed:
                notebook_append(
                    self.paths.notebook,
                    f"## {now_iso()} — force-closed stale campaigns: {closed}\n"
                )
            # Longer sleep when the queue stayed empty — slows the Researcher
            # spin-pump on saturation-driven architectural-only rounds.
            time.sleep(60)
            return {"event": "no_proposal", "added": n_added}
        try:
            outcome = executor.execute(paths=self.paths, proposal=proposal)
        except Exception:
            queue_requeue_inflight(self.paths.queue)
            raise
        else:
            queue_ack(self.paths.queue)
        if outcome.get("ok"):
            state = load_state(self.paths.state)
            state["last_success_ts"] = now_iso()
            save_state(self.paths.state, state)
        self._maybe_digest()
        self._maybe_code()
        self._maybe_write()
        return {"event": "ran", "added": n_added, "outcome_ok": outcome.get("ok"), "name": outcome.get("name")}

    def _record_step_failure(self, e: Exception) -> None:
        import traceback as _tb
        tb = _tb.format_exc(limit=12)
        # Traceback goes to a separate file because notebook_append
        # post-processes entries and triple-backtick blocks don't
        # always survive — having the raw trace on disk is more useful
        # for debugging than a possibly-truncated notebook entry.
        (self.paths.root / "last_traceback.txt").write_text(tb)
        notebook_append(
            self.paths.notebook,
            f"## {now_iso()} — orchestrator step FAILED: {type(e).__name__}: {e} "
            f"(see lab/last_traceback.txt)\n",
        )

    def run(self, *, max_iterations: int | None = None) -> None:
        i = 0
        backoff = GENERIC_BACKOFF_START_S
        try:
            while not self._stop:
                if max_iterations is not None and i >= max_iterations:
                    break
                try:
                    self.step()
                    backoff = GENERIC_BACKOFF_START_S
                    self._check_stall()
                except BudgetExhausted as e:
                    self._handle_budget_exhausted(e)
                except Exception as e:
                    self._record_step_failure(e)
                    kind, retry_after = classify_provider_error(e)
                    if kind in {"credit", "auth"}:
                        # Retrying the researcher loop cannot fix a missing
                        # key or an empty balance; only the owner can.
                        self._halt(kind if kind == "auth" else "no credit", str(e))
                        if self._wait_for_provider(kind):
                            self._resume(f"provider probe succeeded after {kind} halt")
                    elif kind == "rate_limit":
                        wait = retry_after if retry_after is not None else backoff
                        wait = min(max(wait, 1.0), BACKOFF_CAP_S)
                        notebook_append(
                            self.paths.notebook,
                            f"## {now_iso()} — rate limited; backing off {wait:.0f}s\n",
                        )
                        self._interruptible_sleep(wait)
                        backoff = min(backoff * 2, BACKOFF_CAP_S)
                    else:
                        # Cool-off then continue, doubling up to the cap.
                        self._interruptible_sleep(backoff)
                        backoff = min(backoff * 2, BACKOFF_CAP_S)
                i += 1
        except Exception as e:
            notebook_append(
                self.paths.notebook,
                f"## {now_iso()} — orchestrator CRASHED: {type(e).__name__}: {e}\n",
            )
            self._notify_event(
                "crash", "crashed", f"{type(e).__name__}: {e}", priority=5, sound=True,
            )
            raise
        notebook_append(self.paths.notebook, f"## {now_iso()} — orchestrator stopped after {i} iters\n")
        # Don't push "stopped" if we're just restarting for a Coder commit —
        # the user already got "code committed; restarting" 2s ago.
        if not self.restart_requested:
            notify_all(title=f"{_lab_label()} stopped", message=f"orchestrator exited after {i} iterations")
