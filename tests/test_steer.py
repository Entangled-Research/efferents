"""Owner steering: charter + steering.jsonl records, the daemon hook
(ack, notebook, pause/resume halt state), and hypothesis supersession."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from efferents import lab as lab_mod
from efferents import steer
from efferents.agents import orchestrator as orch
from efferents.agents.state import campaign_open_list, load_state
from efferents.cli import main
from efferents.lab import LabConfig, SubmissionError

SAMPLE = Path(__file__).parent / "fixtures" / "sample_submission"

OLD_HYP = (
    "---\nslug: old-claim\nfalsifiability_gate: passed\nstatus: active\n---\n\n"
    "# Old claim\n\n## Claim\n\nThe old operational claim.\n\n"
    "## Falsifier(s)\n\n- It fails.\n"
)
NEW_HYP = (
    "---\nslug: new-claim\nfalsifiability_gate: passed\nstatus: active\n"
    "supersedes: old-claim\n---\n\n"
    "# New claim\n\n## Claim\n\nA generative restatement.\n\n"
    "## Falsifier(s)\n\n- It fails differently.\n"
)


@pytest.fixture
def sub(tmp_path, monkeypatch):
    monkeypatch.setenv("EFFERENTS_HOME", str(tmp_path / "home"))
    d = tmp_path / "sub"
    shutil.copytree(SAMPLE, d)
    lab_mod.set_config(LabConfig.from_submission(d))
    return d


@pytest.fixture
def harness(sub, monkeypatch):
    o = orch.Orchestrator(lab_dir=sub / "lab", context_dir=sub / "context", dry_run=True)
    sleeps: list[float] = []
    notifications: list[dict] = []
    monkeypatch.setattr(o, "_interruptible_sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(orch, "notify_all", lambda **k: notifications.append(k))
    return SimpleNamespace(o=o, sub=sub, sleeps=sleeps, notifications=notifications)


def _records(lab_root: Path) -> list[dict]:
    return [json.loads(ln) for ln in (lab_root / "steering.jsonl").read_text().splitlines()]


# --- steer: charter + record --------------------------------------------------

def test_steer_appends_charter_entry_and_queued_record(sub):
    text = "Don't repeat qfm/px;\nmake them generative models, use literature."
    charter, ledger = steer.steer(sub, text=text, by="funder:masha")

    assert charter == sub / "context" / "popper.md"
    body = charter.read_text()
    assert "owner steering" in body
    assert "funder:masha" in body
    assert "> Don't repeat qfm/px;" in body and "> make them generative models" in body

    assert ledger == sub / "lab" / "steering.jsonl"
    (rec,) = _records(sub / "lab")
    assert rec["text"] == text and rec["by"] == "funder:masha"
    assert rec["ack"] is None and "action" not in rec and rec["ts"]

    # A second steer appends; earlier charter entry is untouched.
    steer.steer(sub, text="second", action="pause")
    assert charter.read_text().count("# Lab charter") == 1
    assert "> Don't repeat qfm/px;" in charter.read_text()
    assert "owner steering: pause" in charter.read_text()
    assert [r.get("action") for r in _records(sub / "lab")] == [None, "pause"]
    # Steering never touches the hypothesis.
    assert (sub / "hypothesis.md").read_text() == (SAMPLE / "hypothesis.md").read_text()


def test_steer_rejects_empty_text(sub):
    with pytest.raises(steer.SteeringError, match="empty"):
        steer.steer(sub, text="   ")


def test_cli_steer_text_file_and_pause(sub, capsys):
    assert main(["steer", "--submission", str(sub), "redirect to generative models"]) == 0
    out = capsys.readouterr().out
    assert f"charter={sub / 'context' / 'popper.md'}" in out
    assert "next step" in out

    note = sub / "note.md"
    note.write_text("Longer steering text\nfrom a file.\n")
    assert main(["steer", "--submission", str(sub), "--file", str(note), "--by", "PI"]) == 0
    assert main(["steer", "--submission", str(sub), "--pause"]) == 0
    assert main(["steer", "--submission", str(sub), "--resume", "budget review done"]) == 0

    recs = _records(sub / "lab")
    assert [r.get("action") for r in recs] == [None, None, "pause", "resume"]
    assert recs[1]["text"] == "Longer steering text\nfrom a file." and recs[1]["by"] == "PI"
    assert recs[2]["text"] == "pause requested by lab owner"
    assert "> from a file." in (sub / "context" / "popper.md").read_text()


def test_cli_steer_requires_text_or_action(sub, capsys):
    assert main(["steer", "--submission", str(sub)]) == 2
    assert "--pause" in capsys.readouterr().err


# --- daemon hook ---------------------------------------------------------------

def test_hook_acks_records_writes_notebook_and_state(harness):
    o, sub = harness.o, harness.sub
    steer.steer(sub, text="use literature", by="funder")
    steer.steer(sub, text="second note")

    done = steer.apply_pending(o)
    assert [r["text"] for r in done] == ["use literature", "second note"]

    notebook = o.paths.notebook.read_text()
    assert f"## {done[0]['ts']} — owner steering: use literature" in notebook
    assert "owner steering: second note" in notebook
    assert load_state(o.paths.state)["steering"] == {
        "ts": done[1]["ts"], "by": "lab owner", "text": "second note",
    }
    recs = _records(sub / "lab")
    assert all(r["ack"] for r in recs) and recs[0]["text"] == "use literature"
    # Nothing left to do; state and notebook unchanged on the next pass.
    assert steer.apply_pending(o) == []
    assert steer.step_hook(o) is False and harness.sleeps == []


def test_hook_pause_and_resume_toggle_owner_halt(harness):
    o, sub = harness.o, harness.sub
    steer.steer(sub, text="hold spend until Friday", by="funder", action="pause")

    assert steer.step_hook(o) is True
    assert harness.sleeps == [60.0]
    state = load_state(o.paths.state)
    assert state["status"] == "paused"
    assert state["halt_reason"] == "owner: funder: hold spend until Friday"
    assert (o.paths.root / "halt_reason.txt").read_text().startswith("owner: funder:")
    assert harness.notifications and harness.notifications[0]["priority"] == 5
    assert "HALT (owner)" in o.paths.notebook.read_text()
    # The orchestrator step short-circuits while the owner pause holds.
    assert o.step() == {"event": "owner_paused", "added": 0}
    assert len(harness.sleeps) == 2

    # `efferents start` clears the halt file; the hook restores it while paused.
    (o.paths.root / "halt_reason.txt").unlink()
    assert steer.step_hook(o) is True
    assert (o.paths.root / "halt_reason.txt").exists()

    steer.steer(sub, text="go", by="funder", action="resume")
    assert steer.step_hook(o) is False
    state = load_state(o.paths.state)
    assert state["status"] == "running" and "halt_reason" not in state
    assert not (o.paths.root / "halt_reason.txt").exists()
    assert "resumed: owner steering by funder: go" in o.paths.notebook.read_text()
    assert all(r["ack"] for r in _records(sub / "lab"))


def test_resume_does_not_clear_a_budget_halt(harness):
    o, sub = harness.o, harness.sub
    o._halt("budget", "daily cap")
    steer.steer(sub, text="resume please", action="resume")
    assert steer.step_hook(o) is False  # not an owner pause, so no idle
    state = load_state(o.paths.state)
    assert state["status"] == "paused" and state["halt_reason"].startswith("budget:")


def test_hook_skips_corrupt_lines(harness):
    o, sub = harness.o, harness.sub
    (sub / "lab").mkdir(exist_ok=True)
    (sub / "lab" / "steering.jsonl").write_text("not json\n")
    steer.steer(sub, text="fine")
    assert [r["text"] for r in steer.apply_pending(o)] == ["fine"]


# --- supersession ---------------------------------------------------------------

def _prepare_supersession(sub: Path) -> Path:
    (sub / "hypothesis.md").write_text(OLD_HYP)
    corpus_old = sub / "popper-corpus" / "old-claim" / "hypothesis.md"
    corpus_old.parent.mkdir(parents=True)
    corpus_old.write_text(OLD_HYP)
    new = sub / "popper-corpus" / "new-claim" / "hypothesis.md"
    new.parent.mkdir(parents=True)
    new.write_text(NEW_HYP)
    return new


def test_supersede_installs_successor_marks_old_and_records(sub):
    new = _prepare_supersession(sub)
    (sub / "lab").mkdir()
    (sub / "lab" / "hypothesis.md").write_text(OLD_HYP)

    res = steer.supersede(sub, new, by="funder", note="pivot to generative models")

    assert res["old_slug"] == "old-claim" and res["new_slug"] == "new-claim"
    assert (sub / "hypothesis.md").read_text() == NEW_HYP
    assert (sub / "lab" / "hypothesis.md").read_text() == NEW_HYP
    # Retired corpus copy: frontmatter gains superseded_by, body byte-identical.
    old_text = (sub / "popper-corpus" / "old-claim" / "hypothesis.md").read_text()
    fm, _, body = old_text[4:].partition("\n---\n")
    assert "superseded_by: new-claim" in fm
    assert body == OLD_HYP[4:].partition("\n---\n")[2]
    # The successor file itself is untouched.
    assert new.read_text() == NEW_HYP

    charter = (sub / "context" / "popper.md").read_text()
    assert "owner supersession: old-claim -> new-claim" in charter
    assert res["old_hash"] in charter and res["new_hash"] in charter
    assert "> pivot to generative models" in charter

    (rec,) = _records(sub / "lab")
    assert rec["action"] == "supersede" and rec["ack"] is None
    assert rec["old_slug"] == "old-claim" and rec["new_hash"] == res["new_hash"]
    assert rec["question"] == "A generative restatement."

    # The retired file now fails validation, pointing at the successor.
    with pytest.raises(SubmissionError, match="superseded_by='new-claim'"):
        lab_mod._parse_hypothesis(sub / "popper-corpus" / "old-claim" / "hypothesis.md")
    # The submission loads on the successor.
    cfg = LabConfig.from_submission(sub)
    assert cfg.hypothesis_slug == "new-claim" and cfg.hypothesis_supersedes == "old-claim"


def test_supersede_requires_matching_supersedes_and_passed_gate(sub):
    new = _prepare_supersession(sub)
    new.write_text(NEW_HYP.replace("supersedes: old-claim", "supersedes: other"))
    with pytest.raises(steer.SteeringError, match="supersedes='other'; expected the current slug 'old-claim'"):
        steer.supersede(sub, new)
    assert (sub / "hypothesis.md").read_text() == OLD_HYP  # nothing installed

    new.write_text(NEW_HYP.replace("falsifiability_gate: passed", "falsifiability_gate: failed"))
    with pytest.raises(steer.SteeringError, match="falsifiability_gate='failed'"):
        steer.supersede(sub, new)

    new.write_text(NEW_HYP.replace("supersedes: old-claim\n", ""))
    with pytest.raises(steer.SteeringError, match="supersedes=None"):
        steer.supersede(sub, new)

    with pytest.raises(steer.SteeringError, match="not found"):
        steer.supersede(sub, sub / "missing.md")
    assert not (sub / "lab" / "steering.jsonl").exists()
    corpus_old = (sub / "popper-corpus" / "old-claim" / "hypothesis.md").read_text()
    assert "superseded_by" not in corpus_old


def test_mark_superseded_is_idempotent_and_refuses_rewrite(tmp_path):
    p = tmp_path / "hypothesis.md"
    p.write_text(OLD_HYP)
    steer.mark_superseded(p, "a")
    once = p.read_text()
    steer.mark_superseded(p, "a")
    assert p.read_text() == once
    with pytest.raises(steer.SteeringError, match="already superseded_by"):
        steer.mark_superseded(p, "b")


def test_hook_opens_successor_campaign_on_supersede(harness):
    o, sub = harness.o, harness.sub
    new = _prepare_supersession(sub)
    res = steer.supersede(sub, new)

    steer.apply_pending(o)
    opens = campaign_open_list(o.paths.runs_db, "sample-conjecture")
    assert len(opens) == 1
    c = opens[0]
    assert c["id"] == "submission-" + res["new_hash"].split(":")[1][:12]
    assert c["hypothesis_hash"] == res["new_hash"]
    assert c["question"] == "A generative restatement."
    assert c.get("headline_metric") == "synthetic_loss"
    assert "opened campaign submission-" in o.paths.notebook.read_text()
    assert "owner steering (supersede)" in o.paths.notebook.read_text()

    # Re-acking the same supersession (e.g. a second record) does not duplicate.
    steer.record_steering(o.paths.root, text="again", by="x", action="supersede", **{
        k: res["record"][k] for k in ("old_slug", "new_slug", "old_hash", "new_hash", "question")
    })
    steer.apply_pending(o)
    assert len(campaign_open_list(o.paths.runs_db, "sample-conjecture")) == 1


def test_cli_supersede(sub, capsys):
    new = _prepare_supersession(sub)
    rc = main(["steer", "--submission", str(sub), "--supersede", str(new), "--by", "PI"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "superseded old-claim" in out and "-> new-claim" in out
    assert "marked superseded_by in" in out
    assert f"charter={sub / 'context' / 'popper.md'}" in out
    assert (sub / "hypothesis.md").read_text() == NEW_HYP

    # A second attempt: the current slug is now new-claim, so it no longer matches.
    rc = main(["steer", "--submission", str(sub), "--supersede", str(new)])
    err = capsys.readouterr().err
    assert rc == 1 and "steer failed" in err
