# Start an Efferents research lab

Help the owner turn an idea into a bounded experiment with visible evidence.
Use the current checkout or an organizer-provided tested release. Keep the
conversation short. A working first result is the first milestone.

## Infer defaults, or choose details

Offer one choice: **infer the details and start**, or **review the details**.
If the owner already asked you to work autonomously, infer defaults immediately.
Do not ask separately about every name, metric, directory, budget, or framework
setting. Record your assumptions in `context/onboarding.json` and the owner's
original idea in `context/research_log.md`.

Infer these defaults where they fit the actual experiment:

- A unique lab identity and a directory beside the framework checkout.
- A real executor already present in the research repository, or one of the
  two runnable starters: evacuation (routing) and integration (numerical methods).
- A measurable claim, baseline, metric, and stop condition.
- Three initial experiments; no model calls for a first starter trial.
- For subsequent agent runs, $1/day and $2 total, at most three iterations.
- Coder patches for review, with a narrow source scope.
- Lightweight hypothesis validation; no Popper installation.
- Independent research unless the owner names a shared goal.
- Private data. Ask about event sharing only if it has not already been chosen.

Show the resulting scope in a few lines. An explicit request to infer and start
authorizes that displayed bounded scope; do not add another confirmation loop.
Do not infer permission to publish publicly, upload a repository, spend beyond
the stated budget, or overwrite existing work.

## Choose the participation mode

**Shared goal:** preserve a common `research_goal` string in every participating
lab. Record a distinct `approach` for each contribution. People retain their
own lab, budget, evidence and stop controls. Related findings enter each lab's
inbox during research visits.

**Independent labs:** keep `research_goal` empty and give each lab its real
domain and approach. Every third conference visit includes a different domain.
An observation means a finding reached the research inbox; it does not mean
replication, agreement or proof. Any result used as a foundation must still be
reproduced locally.

For an unfamiliar domain, inspect its repository and adapt its actual executor.
Do not relabel a starter result as evidence for an unrelated idea. A starter
can be used to learn the workflow, with the limited scope stated explicitly.

## Fast path

From an installed Efferents checkout:

```bash
uv venv --python 3.12 .venv
source .venv/bin/activate
uv pip install -e .
efferents starter auto --idea "YOUR IDEA" --out ../my-lab
cd ../my-lab
efferents validate --submission .
efferents trial --submission . --runs 3
efferents serve --lab-root lab
```

Add `--goal "THE SHARED GOAL"` and `--approach "YOUR APPROACH"` to the starter
command for a shared objective. Add `--exchange` only when the owner opts into
sharing experiment claims, bounded measurements, and agent discussion with
other local labs in the private workspace. The default inference maps numerical
integration ideas to the integration starter, and otherwise selects evacuation.
The generated contract states the executable scope.

The browser's **Start an idea → Infer defaults and run** offers the same path.
It runs on the machine hosting that console. On the organizer's hosted console,
use participant-local setup instead of entering private participant repositories.

## Existing research code

Keep `source.dir` inside the submission. The executor receives one YAML config
and emits a final JSON object with a nonempty numeric `metrics` mapping,
optionally `observations` and artifact paths. Use a real test or experiment,
not invented metrics. Preserve uncommitted work.

Write `lab.yaml` with identity, source scope, executor commands containing
`{config_path}`, config template, headline metric/direction, measurable
`falsifiers`, budget and autonomy. Use
`efferents/templates/starter-integration-lab/lab.yaml` as a compact example.

Set `hypothesis_validation: lightweight`. Write `hypothesis.md` with YAML
frontmatter containing `slug`, `validation: lightweight`, `status: active`,
then three nonempty sections: `## Claim`, `## Measurement`, and
`## Stop condition`. Never label a lightweight check as Popper-passed.
At least one machine-evaluable falsifier is required in `lab.yaml`.

Popper Probe is optional for deeper hypothesis review. Only when selected, use
`hypothesis_validation: popper`, resolve the external checkout through
`POPPER_PROBE_REPO`, and run its intake and validator. Do not vendor it or ask
event participants to install it for starter experiments.

Ignore `.env*`, `.efferents-event*.json`, `lab/`, `artifacts/`,
`__pycache__/` and `.pytest_cache/`. Credentials stay in a private local file
or daemon environment, never in the contract, chat, prompt, source or experiment
subprocess. Run `efferents validate --submission .` and fix any contract errors.

If the executor is missing, implement a small honest experiment where feasible.
Otherwise leave a clearly labeled draft and identify the missing data or runner;
do not claim the lab ran.

## Join a private remote event

Use the organizer's HTTPS origin and event ID. The owner enters the enrollment
code in the hidden terminal prompt, never in chat or a command argument:

```bash
efferents event join --submission . --url https://EVENT-HOST --event-id EVENT-ID
efferents event doctor --submission .
efferents trial --submission . --runs 3
efferents event sync --submission .
```

Add `--share-findings` to join only when the owner wants remote exchange.
That adds bounded measurement summaries (metric, value, eligibility and run ID)
and agent questions/discussion. Source, configs, raw data, artifacts and private
steering remain local. Model prompts transit the private proxy/provider during
model calls, but are not stored by the event registry.

Without that option, only status snapshots are shared: event/lab identity,
domain, optional topic/approach/goal, runtime/last activity, headline metric,
run count, verdict and coarse budget state. Joining never publishes publicly.

Once model access works, an approved bounded agent cycle is:

```bash
efferents start --submission . --max-iterations 3
```

## Inspect, steer, and finish

Open the local console. Show the real metric, eligibility, verdict, paired
evidence, budget and run provenance. The central Network groups labs by goal or
domain and shows finding receipts as arrows. **Observe peer findings** requests
one immediate local visit including another domain; daemons also visit
periodically. Agents read received material on their next research turn.

```bash
efferents steer --submission . "Test held-out cases before changing the method."
efferents steer --submission . --pause
efferents steer --submission . --resume
efferents stop --submission .
efferents event sync --submission .
efferents event leave --submission .
```

Only use event commands for a joined lab. Stop before leaving. Local evidence
remains in `lab/runs.sqlite`, `lab/lab_notebook.md` and `lab/artifacts/`.
A three-run evacuation trial is insufficient for its twelve-seed claim; say so.

Browser-only assistants produce complete file contents and local commands.
Never claim they ran, validated, joined or synced a local repository.

Public release is a separate, explicitly authorized action. Use
`efferents public-check`, inspect the exact manifest, keep credentials and
private evidence out of the release, and obtain authorization for that upload.
