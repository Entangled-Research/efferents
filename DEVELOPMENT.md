# Development & architecture

Internal notes for contributors. Buyer-facing copy lives in [`README.md`](./README.md).

> *Each lab is an efferent channel — emitting its outgoing signals (papers,
> claims, corroborations) into a network of labs.*

## Maturity

What works today:
- **Offline demo** (`efferents demo`) — deterministic, no API calls.
- **Repo-adapter run** (`efferents run <repo>`) — executes a real bounded
  train/eval sweep from an `efferents.yaml`, fully offline, writing the journal /
  runs / claims / dashboard with provenance (`efferents/runner.py`).
- **CLI** — `validate / start / status / stop / list / serve / demo / run /
  public-check`.
- **`LabConfig`** — loads a lab from `lab.yaml` + `hypothesis.md`, lab-agnostic.
- **Repo adapter** — `efferents.yaml` loader (`efferents/repo_adapter.py`).
- **Local lab workspace** — `efferents serve` connects a GitHub/local
  submission, records steering, explicitly starts/stops the daemon, and
  visualizes progress.
- **Tests** — see `tests/` (run `uv run pytest tests/`).

### `efferents run` execution contract

The runner sweeps `sweep.param` over `sweep.values`. Per iteration it writes a
config (`config_template` overlaid with the swept value), runs `train_command`
(with `{config_path}` substituted), reads the checkpoint path from train's
trailing stdout JSON (`{"checkpoint": "..."}`), runs `eval_command` (with
`{checkpoint}` substituted), and reads `metrics.<metric>` from eval's trailing
JSON. All checkpoints/configs/logs land under the `--out` dir, never the target
repo. Budget is tracked as a wall-clock GPU-hour proxy; LLM spend is $0 (offline).
`approval.mode: dry_run` writes the plan and stops before executing.

In progress: the live multi-agent loop runs but several agent prompts still
carry assumptions from the original domain-specific lab and read oddly in other
domains; broader domain coverage and prompt templating are ongoing.

## Package layout

```
efferents/
├── efferents/                 # the framework package
│   ├── __main__.py            # `python -m efferents …` → cli.main
│   ├── cli.py                 # validate / start / status / stop / list / serve / demo
│   ├── demo.py                # offline deterministic product demo
│   ├── runner.py              # `efferents run`: execute a repo adapter sweep
│   ├── publication.py         # fail-closed public repository release preflight
│   ├── lab.py                 # LabConfig (lab.yaml + hypothesis.md loader)
│   ├── repo_adapter.py        # efferents.yaml ("bring your own repo") loader
│   ├── daemon.py              # foreground / detached run loop
│   ├── registry.py            # running-lab registry
│   ├── dashboard/             # local connect / steer / observe HTTP workspace
│   ├── journal/               # paper/memo feed renderer
│   ├── migrations/            # idempotent SQLite schema migration
│   ├── schemas/               # pydantic paper-bundle schema
│   └── agents/                # the multi-role agent loop
│       ├── orchestrator.py    # the 24/7 loop
│       ├── budget.py          # cost discipline + cache-hit tracking
│       ├── state.py           # file-based state (SQLite + JSONL + md)
│       ├── researcher.py      # student↔supervisor dialogue, modes, campaigns
│       ├── coder.py           # autonomous source modification (scoped to source.dir)
│       ├── analyst.py         # periodic digest + flat-improvement counter
│       ├── writer.py          # memo composition, novelty/gain gate, peer review
│       ├── reviewer.py        # reviewer board
│       ├── librarian.py       # lit-review subagent
│       ├── popper_gate.py     # headless popper-probe intake
│       └── progress.py        # self-contained HTML dashboard
├── examples/
│   ├── smoke-lab/             # a complete domain-agnostic example lab (stub executor)
│   └── repo-adapter/          # efferents.yaml example for an existing ML repo
├── tests/
└── docs/
    ├── superpowers/specs/     # design specs
    ├── superpowers/plans/     # implementation plans
    └── templates/             # reference-lab.py.example (historical reference)
```

## Two config layers

1. **`efferents.yaml` (repo adapter)** — the buyer front door. A flat config at
   an ML repo root: goal, train/eval commands, metric, budget, approval. Loaded
   by `efferents.repo_adapter.RepoAdapterConfig`.
2. **`lab.yaml` + `hypothesis.md` (submission)** — the lower-level lab schema the
   daemon consumes directly. Loaded by `efferents.lab.LabConfig.from_submission`.
   The smoke lab and the `intake.md` trial use this layer.

## How a lab runs

One YAML config = one run. The framework hands the lab's executor a config path;
the executor produces a SQLite/JSONL row with metrics. State is file-based (no DB
server): lab state under `lab/`, popper hypotheses under `popper-corpus/`. Every
Anthropic call goes through `efferents.agents.budget` (Sonnet default; Opus on
the Analyst and on Researcher escalation).

## Local workspace control boundary

`efferents serve` binds only to `127.0.0.1`. Its entry page accepts a GitHub
repository/README URL or an absolute local path. Connection may clone, validate,
initialize file-backed state, and register the stopped lab; it does not execute
repository commands. Starting and stopping are separate POST requests gated by
a per-server CSRF token and an explicit confirmation in the UI. Steering only
appends a timestamped `Human steering` block to
`context/research_log.md`; the Researcher consumes it on a later pass.

The browser never receives an API-key value, only a boolean indicating whether
one is available. Static assets use a strict script CSP, responses are
non-cacheable, and execution remains on the user's machine.

## The offline demo

`efferents/demo.py` fakes the *agent reasoning* with deterministic text but runs
the lab's *real* run command over a small parameter sweep, recording the actual
metric each run emits (with an analytic fallback so a fresh clone never
hard-fails). It exists for product comprehension and to exercise the
journal/runs/claims/dashboard artifact shape without a paid API.

## The multi-lab vision (forward design)

The long-term destination is a platform where many autonomous labs publish to
shared **venues** and interact (corroborate, challenge, cite) agent-to-agent,
hosted at `efferents.com`. That is **out of scope** for the framework package —
see [`context/journal_vision.md`](./context/journal_vision.md). Build the
single-lab library first.

## History: the original reference lab

Efferents was extracted on 2026-05-26 from an earlier domain-specific research
lab. The framework-relevant subset was copied out and decoupled from that
domain's assumptions into `LabConfig`. Some agent prompts still carry historical
phrasing; `docs/templates/reference-lab.py.example` records how the original lab
parameterized the framework. Do **not** reintroduce imports from the predecessor
package.

## Related projects

- [popper-probe](https://github.com/mashathepotato/popper-probe) — adversarial
  Popperian hypothesis intake; required as a subprocess by
  `efferents.agents.popper_gate`. Path via `POPPER_PROBE_REPO`
  (default `~/Documents/popper-probe`).
