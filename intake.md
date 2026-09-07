# Launch an efferents research lab

You are the setup agent for a human who wants to create an autonomous research
lab with efferents. Work conversationally, keep the human informed, and follow
this file in order.

The outcome is:

- a local lab configured around the human's code and compute;
- a first hypothesis that has passed a falsifiability gate;
- a bounded first run or an explicitly reviewed plan for one; and
- a deliberate placement choice: a private research group or a public lab.

The lab is **private by default**. Do not upload research, source code, data,
logs, hypotheses, papers, credentials, or metrics unless the human explicitly
chooses the public path and approves the exact artifact being published.

## 0. Explain the boundary

Tell the human:

> Efferents runs on your machine, against commands and a budget you approve.
> We will create the lab privately first. After you review the first hypothesis,
> you can keep it inside your private research group or choose to make the lab
> public. Public means selected research artifacts are published; it does not
> expose your filesystem, data, secrets, or full repository.

Do not start repository commands yet.

## 1. Identify the starting point

Inspect the current directory and determine which path applies:

0. **The efferents framework checkout itself** — the directory contains
   `efferents/`, `pyproject.toml`, and this `intake.md`. Do not create a lab
   here. Ask the human where the lab should live before running anything: a
   fresh directory elsewhere (path 2), an existing research repository
   (path 1), or a framework contribution (path 3).
1. **Existing research repository** — use its root as the submission directory.
   Confirm it is a git repository and show the human any uncommitted changes.
   Never discard or overwrite their work.
2. **Fresh research lab** — ask for a short lab name, create a new directory,
   initialize git, and create the minimal executor/config layout after Step 3.
3. **Framework contributor** — if the human wants to modify efferents itself,
   clone `https://github.com/mashathepotato/efferents` and install it editable.

The lab name becomes `lab_id` and must match
`[A-Za-z0-9][A-Za-z0-9._-]{0,127}`: a letter or digit first, then only
letters, digits, `.`, `_`, `-`; no spaces or slashes. Prefer kebab-case
(`protein-folding-lab`), which also matches the hypothesis slug convention.

For an existing or fresh lab, the submission directory will contain
`README.md`, `lab.yaml`, `hypothesis.md`, and the experiment code. The code
under `source.dir` must be inside the submission directory. If the human's
experiment code lives elsewhere, copy (vendor) it into the submission before
Step 4; do not point `source.dir` outside the submission. The minimal shape is
`examples/smoke-lab/`:

```text
<submission>/
  README.md  lab.yaml  hypothesis.md
  src/          # source.dir: what the run command executes
  configs/      # config_template the lab mutates per run
  context/      # charter (popper.md) and research log
  popper-corpus/<slug>/hypothesis.md
```

## 2. Install the framework

Efferents requires Python 3.10 or newer. Prefer `uv`:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python "git+https://github.com/mashathepotato/efferents.git"
.venv/bin/efferents --help
```

If this is an editable framework checkout:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e .
.venv/bin/efferents --help
```

If `uv` is unavailable, use an explicitly selected Python 3.10+ interpreter
and `python -m venv .venv`. Do not assume the system `python3` is new enough.

The help output must include `validate`, `start`, `status`, `stop`, `steer`,
and `serve`.
If installation is blocked by the agent's permission policy, ask the human to
approve the install or run the displayed command themselves.

## 3. Create the first falsifiable hypothesis

Ask the human for the research claim or open question they want the lab to
start from. Ask one question at a time.

Use the `popper-probe:intake` skill if it is installed. For Claude Code, its
installation commands are:

```text
/plugin marketplace add mashathepotato/popper-probe
/plugin install popper-probe@popper
```

After installation, reload plugins before continuing. If the current agent
cannot install Claude Code plugins, read and follow the agent-readable intake
protocol instead:

```text
https://raw.githubusercontent.com/mashathepotato/popper-probe/main/skills/intake/SKILL.md
```

The dialogue must produce `popper-corpus/<slug>/hypothesis.md`. Show the entire
draft to the human and get approval before writing it. Continue only when the
frontmatter contains:

```yaml
falsifiability_gate: passed
status: active
```

Copy the approved file to `<submission>/hypothesis.md`. If the gate fails,
surface the diagnostic and help the human narrow or reformulate the claim; do
not create or start a lab around an unfalsifiable claim.

### 3b. Record the lab charter (`context/popper.md`)

After the gate passes, write `<submission>/context/popper.md` — the lab
charter. It preserves what the probe dialogue decided, so future students and
supervisors inherit the direction instead of rediscovering it. Record:

- the human's initial direction **verbatim, as they first prompted it**
  (`prompted_by:` the human), before any sharpening;
- the design decisions made during the probe dialogue: which formulations
  were rejected and why, which falsifier was chosen over which alternatives,
  what scope was cut;
- a pointer to the gated `hypothesis.md` and its hash.

Use `efferents.agents.popper_gate.write_charter(...)`, or write the file by
hand in the same shape (it creates the framing header automatically). The
charter is a **living document — guidance, not rules**: agents read it to
orient proposals and classify student work, and append amendments when the
direction legitimately moves. Never rewrite earlier entries; the requirement
may change, but the record of what was asked for, and when, must not.
Later campaign gates append to the same file automatically.

## 4. Configure the lab around real execution

Ask for these values one at a time:

1. Lab name (`lab_id`; must match `[A-Za-z0-9][A-Za-z0-9._-]{0,127}`,
   kebab-case preferred) and research domain.
2. Source directory the lab may inspect or modify, relative to the submission
   directory (it must be inside the submission; see Step 1).
3. Allowed file patterns. Default to the narrowest useful set.
4. Run command containing `{config_path}`.
5. Optional smoke command containing `{config_path}`.
6. Config-template path relative to `source.dir`.
7. Headline metric and whether it should be minimized or maximized.
8. Daily LLM budget, and optionally a lifetime cap (`total_cap_usd`, at least
   the daily cap). The lab halts when either is reached.
9. Whether the Coder may modify source files. Default to `false`. If enabled,
   whether it commits edits itself (`coder_mode: auto`) or writes diffs under
   `lab/patches/` for the owner to apply or reject (`coder_mode: review`).
10. Abandonment conditions: which run-ledger columns and thresholds would
    refute the hypothesis (`falsifiers:`, below).

If the human does not yet have a real executor, offer two honest choices:

- create only the validated lab shell and stop before execution; or
- copy the synthetic executor shape from
  `examples/smoke-lab/` to test the plumbing, clearly labelling its results as
  unrelated to the human's scientific claim.

Write `<submission>/lab.yaml`. Use this shape:

```yaml
lab_id: example-lab            # [A-Za-z0-9][A-Za-z0-9._-]{0,127}
domain: example-domain

source:
  dir: .
  allowed_patterns: ["src/**/*.py", "configs/**/*.yaml"]

executor:
  run_command: "python -m src.run --config {config_path}"
  smoke_command: "python -m src.run --config {config_path} --smoke"
  config_template: configs/default.yaml
  run_timeout_s: 7200
  smoke_timeout_s: 300

metrics:
  headline: { column: validation_score, direction: max }
  panels:
    - { column: validation_score, label: "Validation score", direction: max }
  flat_digest_epsilon: 0.005
  # Optional: run columns that split the ledger into buckets for saturation
  # analysis and falsifier evaluation.
  bucket_axes: [model_size]
  # Optional: validity gates; a run failing one is ineligible for best/latest.
  constraints:
    - { column: heldout_gap_pp, op: ">=", value: 5, label: "Held-out gap" }

# Optional: the two comparison arms for seed-paired deltas. `axis` is a
# run/observation column; `labels` map its values to display names.
evidence:
  comparison:
    axis: arm
    labels: { baseline: "Baseline", treatment: "Treatment" }
    order: [baseline, treatment]

# Optional: abandonment conditions the framework evaluates. Each rule reports
# fired | survived | insufficient_data; any fired rule makes the verdict
# "falsified".
falsifiers:
  - id: F1                     # aggregate rule over a ledger column
    description: "Median gain over baseline is not positive"
    when: { column: delta_vs_baseline, agg: median, op: "<=", value: 0, bucket: any, min_n: 4 }
  - id: F2                     # paired rule: bootstrap 95% CI of the median delta
    description: "Seed-paired CI of the delta includes zero"
    when: { column: delta_vs_baseline, ci95_excludes_zero: false }

budget:
  daily_cap_usd: 10.0
  total_cap_usd: 200.0         # optional lifetime cap; at least daily_cap_usd
  sonnet_default: true

autonomy:
  coder_enabled: false
  coder_mode: review           # auto: commit edits; review: diffs to lab/patches/
```

Adapt the values; do not copy placeholders into a real lab. Keep credentials in
the environment or `<submission>/.env`, never in `lab.yaml` or git.

`falsifiers:` encodes the lab's abandonment conditions in a form the framework
checks, not only as prose in `hypothesis.md`. Translate the hypothesis's
falsifier into at least one rule over columns the run command actually emits.
An aggregate rule takes `column`, `agg` (`median | mean | min | max | count |
frac_ge | frac_le`; the `frac_*` forms need `threshold`), `op`, and `value`. A
paired rule takes `ci95_excludes_zero` plus either a per-run delta `column` or
a `metric` differenced across the two `evidence.comparison` arms. `bucket` is
`any` (fires if any bucket fires), `all` (fires only if every bucket fires), or
one value of the first `bucket_axes` column; `min_n` (default 3) is the
minimum number of runs before a rule is decided. The Analyst digest and the
workspace Verdict panel evaluate every rule over succeeded runs and report
`fired | survived | insufficient_data`; the verdict is `falsified` if any rule
fired, `survives` if all survived, otherwise `undecided`.

## 5. Validate and present the launch contract

Run:

```bash
.venv/bin/efferents validate --submission <submission>
```

Fix field-level errors until validation succeeds. Then present a concise launch
contract containing:

- lab id and domain;
- hypothesis title and falsifier;
- source directory and allowed patterns;
- exact run and smoke commands;
- headline metric and direction;
- declared falsifiers, or that none are declared yet;
- Coder enabled/disabled and, if enabled, its mode;
- daily budget and lifetime cap, if any;
- current placement: **private, unlinked**.

Ask for explicit approval before executing any repository-defined command.

## 6. Run the first bounded cycle

If the human wants a no-LLM plumbing check first:

```bash
.venv/bin/efferents start --submission <submission> --dry-run --max-iterations 1
```

If `ANTHROPIC_API_KEY` is available and the human approved real execution:

```bash
.venv/bin/efferents start --submission <submission> --max-iterations 3
```

Do not detach the first run. Keep it bounded so the human can inspect what the
lab does. Then open the workspace:

```bash
.venv/bin/efferents serve --lab-root <submission>/lab
```

Report the local URL. Show the first hypothesis, run ledger, Verdict panel
(falsifier statuses), budget, agent log, and any paper/memo produced. Point out
that every artifact a run reports is copied to
`<submission>/lab/artifacts/<run_id>/<kind>/`, so a later run with the same
parameters cannot overwrite the file a ledger row cites. If no experiment ran,
say so plainly and explain what executor or approval is still missing.

## 6b. Owner steering and control

After the first bounded run, show the human the controls they keep. All of
them run from the submission directory, write only to append-only ledgers, and
never rewrite evidence, the run ledger, or earlier charter entries. None of
them sends anything off the machine except the notification channels the human
configures.

```bash
.venv/bin/efferents status --submission .
.venv/bin/efferents steer  --submission . "Prioritise the small-model buckets; drop the LR sweep."
.venv/bin/efferents steer  --submission . --pause
.venv/bin/efferents steer  --submission . --resume
.venv/bin/efferents steer  --submission . --supersede popper-corpus/<new-slug>/hypothesis.md
.venv/bin/efferents stop   --submission .
```

- `steer "<text>"` (or `--file <path>`) records the text verbatim in the
  charter `context/popper.md` and queues it in `lab/steering.jsonl`; the
  daemon acknowledges it on its next step with a notebook line and
  `state.json["steering"]`. `--by "<name>"` attributes it (default
  `lab owner`).
- `--pause` halts spending with kind `owner`; only `--resume` lifts it.
- `--supersede` retires the current hypothesis in favour of a gated successor
  whose frontmatter carries its own `slug:` and `supersedes: <current slug>`.
  The retired corpus copy gets `superseded_by:`, the successor is installed as
  `hypothesis.md`, and the daemon opens a campaign for it. Evidence gathered
  under the old hypothesis stays in the ledger. A hypothesis marked
  `superseded_by` no longer validates, so it cannot be started by mistake.
- `status --submission .` resolves the lab from `./lab` (registry record or
  `lab/daemon.pid`), so it works when the registry has lost the record. It
  prints `status=`, `pid=`, `last_activity=`, `dashboard=`, `workspace=` (the
  URL while `efferents serve` is running for that root), and `halt_reason=`.
- `stop --submission .` sends SIGTERM (SIGKILL after 10 s) and marks the
  registry record stopped.

**Halts.** The daemon pauses itself in an auditable way: `lab/halt_reason.txt`
holds `<kind>: <reason>`, `lab/lab_notebook.md` gets a `HALT (<kind>)` line,
and `state.json` reads `status: paused`. `status` shows it as `halt_reason=`;
`start` clears it. Kinds:

- `budget` — the daily cap was reached: sleep until the next UTC day, then
  resume. The lifetime cap (`budget.total_cap_usd`, or env
  `EFFERENTS_TOTAL_CAP_USD` when the config sets none) was reached: halt and
  exit; only the owner can raise it and restart.
- `auth` / `no credit` — the provider rejected the key or the balance. The lab
  halts and re-probes the provider with a minimal request every 5 min,
  doubling to 1 h, and resumes when a probe succeeds. It does not loop on
  research calls in the meantime.
- `owner` — `steer --pause`.

Rate limits and other step failures back off (1 min doubling to 1 h) without
halting. After `EFFERENTS_STALL_HOURS` (default 6) without a successful run the
owner is notified; the lab keeps running.

**Notifications.** Halts, stalls, crashes, and pauses fire a macOS banner and,
if set, `NTFY_TOPIC` (ntfy.sh; treat the topic as a secret) and
`EFFERENTS_WEBHOOK_URL` (POST JSON `{title, message, lab_id, ts}`), at most
one per event per hour. Put them in the daemon environment or
`<submission>/.env`; `NTFY_TOPIC` cannot be listed in
`executor.env_passthrough`. Digests also include a blind ranking of up to
`EFFERENTS_REVIEW_IMAGES` (default 6; `0` disables) image artifacts, shown to
the reviewer with letter labels only.

**Coder review and infrastructure blocks.** With `autonomy.coder_mode: review`
the Coder never edits `source.dir`; each proposed change is a diff plus a
rationale under `lab/patches/`, tracked in `lab/patches/patches.jsonl` and
`state.json["pending_patches"]`. A student that cannot make its experiment
valid without an executor change records a block in `lab/blocked.jsonl`
(`state.json["blocked_on_infrastructure"]`).

```bash
.venv/bin/efferents patch --submission . list
.venv/bin/efferents patch --submission . apply  <path>   # git apply; refuses on unstaged changes
.venv/bin/efferents patch --submission . reject <path>
.venv/bin/efferents block --submission . list
.venv/bin/efferents block --submission . resolve <id>
```

`patch apply` leaves the working tree modified but uncommitted: run the smoke
command, then commit. Applying a patch that addressed a block resolves the
block.

## 7. Ask where the lab belongs

Only after the human has reviewed the hypothesis and launch contract, ask:

> Keep this lab in your private research group, or prepare it as a public lab
> on efferents.com?

### Private research group — default

- Keep the full lab state in its isolated local environment.
- Do not publish or sync hypotheses, papers, metrics, logs, source, or data.
- Other labs and other users receive no feed access and share no files with it.
- Explain that team invitations/private hosted group sync are a future platform
  capability; the framework's working privacy boundary today is local storage.

### Public lab — explicit opt-in

- Explain exactly what may eventually be published: lab identity/domain, the
  approved seed hypothesis, accepted paper bundles, metric provenance, and
  optional code repository/commit references.
- Explain what must never be published automatically: credentials, `.env`, raw
  datasets, arbitrary source files, private logs, or unaccepted drafts.
- Run the repository preflight before calling the lab ready to link:

  ```bash
  .venv/bin/efferents public-check <submission>
  ```

  The first clean result is normally `needs_manual_review`. Resolve every
  blocker, show the findings and the rights/privacy/confidentiality/export/
  security attestations to the human, and ask who is taking responsibility for
  that review. Only after they explicitly confirm all attestations, rerun and
  store the auditable report outside the repository:

  ```bash
  .venv/bin/efferents public-check <submission> \
    --acknowledge-manual-review "Reviewer name" \
    --report "$HOME/.efferents/release-reports/<lab-id>.json"
  ```

  Do not continue on `blocked` or `needs_manual_review`. The check reduces
  disclosure risk but is not legal advice and cannot determine copyright
  ownership, lawful personal-data publication, export classification, patent
  strategy, or contractual restrictions for the human.
- Ask the human to approve the publication manifest before any network write.
- The hosted registration/publishing API is not implemented in this framework
  repository yet. Do not claim that the lab was added to efferents.com. Leave
  it private and report it as **ready to link** when the hosted surface ships.

Changing from private to public must always be an explicit later action. Making
a public lab private stops future publication; it cannot silently erase
artifacts that were already made public.

## 8. Handoff

Report:

- lab id and local path;
- first hypothesis path;
- validation and first-run outcome, including the Verdict panel's falsifier
  statuses;
- workspace command (`efferents serve --lab-root lab` from the lab directory)
  and where run provenance lives (`lab/runs.sqlite`, `lab/lab_notebook.md`,
  `lab/artifacts/<run_id>/`);
- the owner controls from Step 6b (`steer`, `--pause`/`--resume`,
  `--supersede`, `status`, `stop`) and any notification variables set;
- placement choice and whether it is local, ready to link, or linked;
- how to start a longer run, only if the human wants one. From the lab
  directory:

```bash
.venv/bin/efferents start  --submission . --detach
.venv/bin/efferents status --submission .
```

The human remains the owner of the lab and can stop it with:

```bash
.venv/bin/efferents stop --submission .
```

`efferents list` shows every registered lab with its status and halt reason.
