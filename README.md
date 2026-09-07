# efferents

**Turn your research repo into an autonomous lab.**

efferents runs bounded experiments on *your* compute and writes auditable
research memos into a local lab journal. It frames a falsifiable hypothesis,
plans an experiment, runs it against your own train/eval commands, and records
every result claim back to a run, a metric, or a code diff.

> Not a chatbot and not an automatic code/data upload. The point is
> **reproducible, budgeted experiment loops** and a **research memory** your team
> actually trusts.

## 1 · Connect a lab

Two ways in, straight from the gateway's Connect page:

**Launch via agent** — open your coding agent in a terminal, inside your
research repo or a fresh folder, and paste one instruction:

```text
Read https://raw.githubusercontent.com/Entangled-Research/efferents/main/intake.md and follow it
```

The agent-facing [`intake.md`](./intake.md) installs efferents, configures the
lab around your code, gates a first hypothesis through an adversarial
[popper-probe](https://github.com/mashathepotato/popper-probe) dialogue, and
runs a bounded first cycle.

**Submit a repo** — paste a GitHub repository/README URL or a local path. A
valid submission has a `README`, `lab.yaml`, and a Popper-passed
`hypothesis.md`. efferents checks it out, validates the contract, and never
executes repository commands during connection.

![The Connect page of the efferents gateway: a Launch-via-agent tab with a copyable one-line instruction, and a Submit-a-repo tab](docs/img/connect-a-lab.png)

## 2 · The lab network

```bash
efferents serve
```

**Network** is the home of the gateway: a map of every lab in the local
registry around the control-plane hub, with a docked rail listing them. The
topbar shows the summed spend and daily caps across all labs. Clicking a lab —
in the rail or on the map — opens it as a tab, VS Code style, next to the
permanent NETWORK tab, and open tabs persist across reloads.

![The efferents network view: a full-width map of local labs around the control-plane hub, with the docked lab rail and the lab tab strip](docs/img/lab-network.png)

## 3 · Audit a lab

Each lab tab is the audit surface:

- **Hypothesis** with its explicit falsification condition.
- **Validity-aware metrics** — best/latest eligible values, median, IQR, and a
  trend chart; excluded runs stay visible but never count.
- **Run ledger** — every run with its ID, timestamp, metric, and a validity
  stamp (best / ties best / eligible / excluded).
- **Evidence** — the lab's own eval suite rendered as eligibility gates
  (`heldout_gap_pp >= 5`) over matched comparisons and visual artifacts.
- **Verdict** — every `falsifiers:` rule from `lab.yaml` evaluated over the
  succeeded runs as `fired | survived | insufficient_data`, and the resulting
  `falsified | survives | undecided`.
- **Steer** — funder direction recorded in an append-only log, read at the next
  agent pass. Starting and stopping spend requires explicit confirmation, and
  the topbar switches to that lab's own budget.

Every nontrivial claim in a memo points at evidence: a `run_id`, a metric file,
a log, or a code diff — not a vibe. Artifacts a run reports are copied to
`lab/artifacts/<run_id>/<kind>/` at ingest, so a re-run with the same
parameters cannot overwrite the file an earlier ledger row cites; byte-identical
artifacts across comparison arms are flagged on the run.

![A lab page in the efferents gateway: lab tabs, the active hypothesis with its falsification condition, a validity-aware metric panel, and the steering log](docs/img/audit-a-lab.png)

## Try it offline (60 seconds, no API key)

```bash
git clone https://github.com/Entangled-Research/efferents && cd efferents
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -e .
.venv/bin/efferents demo smoke-lab
open efferents-demo/dashboard.html
```

A fully offline, deterministic loop on a toy task that writes the complete lab
journal: `journal/` (hypothesis → plan → results → reviewed memo),
`runs.jsonl`, `claims.jsonl`, and a static evidence dashboard. The demo's
reasoning is canned; the experiment — and every recorded metric — is real.

To point the same bounded loop at your own repo, drop an `efferents.yaml` at
its root ([runnable example](examples/repo-adapter/efferents.yaml)) and run
`efferents run <repo> --approve`. The contract: `train` prints
`{"checkpoint": "<path>"}`, `eval` prints `{"metrics": {"<metric>": <value>}}`.

## Run a live lab

```bash
cp .env.example .env        # choose a model and add its provider key
efferents validate --submission examples/smoke-lab/
efferents start    --submission examples/smoke-lab/
efferents serve
```

Claude is the zero-configuration default (`ANTHROPIC_API_KEY`); any
[LiteLLM model identifier](https://docs.litellm.ai/docs/providers) works via
`EFFERENTS_MODEL`, with per-role overrides (`EFFERENTS_MODEL_CODER`, …). To
open a known lab directly: `efferents serve --lab-root examples/smoke-lab/lab`.
Add `--detach` to `start` to run the daemon in the background; `efferents list`
shows every registered lab.

### Owner controls

All of these address a lab by its submission directory, append to ledgers
under `lab/` and `context/popper.md`, and never rewrite evidence.

```bash
efferents status --submission examples/smoke-lab/   # status, pid, halt_reason, workspace URL
efferents steer  --submission examples/smoke-lab/ "Drop the LR sweep; focus on small models."
efferents steer  --submission examples/smoke-lab/ --pause        # or --resume
efferents steer  --submission examples/smoke-lab/ --supersede path/to/new/hypothesis.md
efferents stop   --submission examples/smoke-lab/
efferents patch  --submission examples/smoke-lab/ list           # Coder diffs awaiting review
efferents block  --submission examples/smoke-lab/ list           # students blocked on infrastructure
```

Steering text is recorded verbatim in the charter and picked up on the
daemon's next step. `--supersede` installs a gated successor whose frontmatter
says `supersedes: <current slug>`; the retired hypothesis is marked
`superseded_by` and can no longer be started. With
`autonomy.coder_mode: review` the Coder writes diffs to `lab/patches/` instead
of editing source; `patch apply <path>` applies one with `git apply` and
records the decision.

## Safety & budget

- **Approval modes:** `plan_then_execute` (default), `dry_run`, `autonomous`
  (sandbox use only).
- **Budgets:** a wall-clock execution guardrail plus an LLM spend ledger.
  `budget.daily_cap_usd` halts the lab until the next UTC day;
  `budget.total_cap_usd` (or `EFFERENTS_TOTAL_CAP_USD`) is a lifetime cap that
  halts and exits.
- **Halts are files:** `lab/halt_reason.txt`, a `HALT` line in
  `lab/lab_notebook.md`, and `state.json`. A rejected key or empty balance
  halts and re-probes the provider with backoff rather than retrying the
  research loop.
- **Falsifiability gate:** no compute is spent on a hypothesis that hasn't
  survived a popper-probe dialogue.
- **Falsifiers:** `lab.yaml` can declare the hypothesis's abandonment
  conditions as rules over run-ledger columns — an aggregate (`median`,
  `frac_ge`, …) compared to a threshold, or a seed-paired bootstrap CI that
  must exclude zero — optionally per bucket of `metrics.bucket_axes`. The
  Analyst digest and the workspace evaluate them on every pass, so "we would
  give up if…" is checked by the framework, not only written in prose.
- **Notifications:** halts, stalls (`EFFERENTS_STALL_HOURS`, default 6), and
  crashes go to a macOS banner plus `NTFY_TOPIC` (ntfy.sh) and
  `EFFERENTS_WEBHOOK_URL` (POST JSON) when set; provider keys and `NTFY_TOPIC`
  never reach experiment commands.
- **Release preflight:** `efferents public-check <repo>` scans for disclosure
  risks before anything leaves the machine — see
  [`docs/PUBLIC_RELEASE_GUARDRAILS.md`](./docs/PUBLIC_RELEASE_GUARDRAILS.md).

Why automate the whole loop? Read the motivating essay,
[The English Muffin Problem](https://medium.com/@mashapotatoes/the-english-muffin-problem-eac4d9951569).

## Contact

Masha Baidachna: [LinkedIn](https://www.linkedin.com/in/masha-baidachna/)

## Acknowledgements

- **Andrej Karpathy** — for [autoresearch](https://github.com/karpathy/autoresearch),
  which laid the foundation for the autonomous-research idea.
- **[moltbook](https://moltbook.com)** — for connecting a network of agents in a
  creative way.
- **[Bob](https://www.youtube.com/shorts/ITmNN6GW80g)** — proprietor of the
  internet's finest english-muffin YouTube short, and a dependable source of
  inspiration.

## License

[Apache-2.0](./LICENSE). © 2026 Masha Baidachna. You can clone, modify, and use
efferents — including internally and commercially — under the terms of the
license.
