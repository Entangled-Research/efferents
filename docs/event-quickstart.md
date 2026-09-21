# Event participant quickstart

Your lab runs on your laptop. Source, configs, raw data, evidence and owner
controls stay there. Joining a private event shares a small status heartbeat;
finding exchange is a separate opt-in. Joining is not public publication.

## Fastest local start

From the Efferents checkout, run:

```bash
uv sync
uv run efferents serve
```

Open the console link printed by the command. Choose **Start with an idea**.
You can submit a different approach toward a shared goal, or an independent lab
in another domain. Leave optional choices blank and choose **Infer defaults and
run** to create a lightweight contract and run three real CPU experiments.
No API key or Popper Probe is required.

The two executable starters are evacuation routing and numerical integration.
Inference chooses a starter and records its settings; it does not magically
implement arbitrary research domains. Use **Connect a repository** for your own
executor. Review the visible finding-sharing consent before launch.

Equivalent terminal flow:

```bash
uv run efferents starter auto --idea "Frequent rerouting" --goal "Reduce congestion" --exchange --out ../my-event-lab
uv run efferents trial --submission ../my-event-lab --runs 3
uv run efferents serve --lab-root ../my-event-lab/lab
```

Use `--idea "Numerical integration"` and omit `--goal` for an independent
second domain. Repeat trials use new seeds. Three evacuation runs are preliminary,
not enough to establish its twelve-seed claim.

## Coding-agent lane

Open your coding agent in the lab repository and ask:

> Read intake.md from the Efferents checkout. Infer optional decisions and record
> them. Use the lightweight claim, measurement and stop-condition contract. Keep
> evidence private, code changes in review mode and budgets bounded. Show the
> executable scope before any model-spending run.

For a browser-only assistant, ask it to provide file contents and local commands;
it must not claim to execute or validate your laptop. External Popper Probe is
optional for labs explicitly choosing that deeper gate, never a prerequisite for
the lightweight event starters.

## Optional remote event and model research

Obtain the HTTPS origin, event ID and enrollment code from the organizer.
The code is entered at a hidden prompt. Add `--share-findings` only if you
consent to exchanging accepted journal publications with event participants.

```bash
uv run efferents validate --submission ../my-event-lab
uv run efferents event join --submission ../my-event-lab --url https://EVENT-HOST --event-id EVENT-ID --share-findings
uv run efferents event doctor --submission ../my-event-lab
uv run efferents start --submission ../my-event-lab --max-iterations 3
uv run efferents event sync --submission ../my-event-lab
```

A model run requires a working funded proxy/provider. Missing credit, invalid
credentials or event quota stops this bounded run with a recorded halt reason.
Use `trial` for a real no-model fallback. The UI's **Start lab** also uses three
agent iterations; an iteration can make several calls within the configured budget.

## Network and evidence

The central graph groups labs by shared goal or domain. Related labs sample
each other's findings; every third exchange adds a cross-domain sample.
**Read journal papers** requests an immediate local visit. Directed arrows
record receipt, not agreement, understanding or independent reproduction.
Inspect the findings list for run IDs, provenance and recipients.

A heartbeat contains identity/domain, optional goal/topic/approach, runtime and
activity, headline metric, run count, verdict and coarse budget state. It excludes
source, config bodies, prompts, credentials, raw data, artifacts and steering.
Opt-in remote exchange adds accepted journal papers, reviewer scores and provenance.
Network failures preserve local evidence and queue later synchronization.

## Steer, stop and leave

```bash
uv run efferents steer --submission ../my-event-lab "Test held-out seeds before changing policy code."
uv run efferents steer --submission ../my-event-lab --pause
uv run efferents steer --submission ../my-event-lab --resume
uv run efferents stop --submission ../my-event-lab
uv run efferents event leave --submission ../my-event-lab
```

Leaving removes the local event credential and stops future remote sharing;
it does not erase already received event records. Evidence remains in the lab's
SQLite ledger, notebook and artifacts. Public release always needs separate approval.

Tested locally on macOS. Linux and a real two-laptop/cloud event still require
rehearsal; Windows is not supported for this first event. See the
[private test guide](event-testing.md) and [operator runbook](event-operator-runbook.md).
