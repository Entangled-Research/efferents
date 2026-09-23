# Event participant quickstart

Your lab runs on your laptop. Source, configs, raw data, evidence and owner
controls stay there. A private event can receive a small status heartbeat and,
with your separate consent, accepted journal papers. Ideas, drafts, run
measurements, reviews and direct messages stay inside their originating lab.
Joining is not public publication.

## Fastest local start

From the Efferents checkout, run:

```bash
uv sync
uv run efferents serve
```

Open the console link printed by the command. Choose **Start with an idea**.
Describe the question you want to pursue. Efferents routes related questions to
a suitable lab as separate research tracks, and unrelated or incompatible work
to a separate lab. Choose **Numerical integration** for a mathematics starter;
the example experiments are bounded executable baselines, not arbitrary tests
of the submitted question. Review the displayed experiment claim and limitation
before starting its three real CPU runs. No API key or Popper Probe is required.

Executable starters cover several bounded domains, including mathematics,
evacuation routing and numerical integration. Inference selects a starter and
records its scope; it does not implement arbitrary research questions. Use
**Connect a repository** for your own executor. Accepted papers are eligible for
the private event journal only after the local three-reviewer board accepts
them. Public release needs separate human authorization.

Equivalent terminal flow:

```bash
uv run efferents starter auto --idea "Frequent rerouting" --goal "Reduce congestion" --exchange --out ../my-event-lab
uv run efferents trial --submission ../my-event-lab --runs 3
uv run efferents serve --lab-root ../my-event-lab/lab
```

Use `--idea "Graph coloring"` or `--idea "Numerical integration"` for a
mathematics lab. Inspect `hypothesis.md` and the recorded `context/onboarding.json`
to see the actual claim, measurement, stop condition and starter settings before
relying on the trial. Repeat trials use new seeds. Three evacuation runs are
preliminary, not enough to establish its twelve-seed claim.

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
consent to submitting accepted journal publications to the private event venue.

```bash
uv run efferents validate --submission ../my-event-lab
uv run efferents event join --submission ../my-event-lab --url https://EVENT-HOST --event-id EVENT-ID --share-findings
uv run efferents event doctor --submission ../my-event-lab
uv run efferents start --submission ../my-event-lab --max-iterations 3
uv run efferents event sync --submission ../my-event-lab
```

A model run requires a working funded proxy/provider. Missing credit, invalid
credentials or event quota stops this bounded run with a recorded halt reason.
For joined event labs, model-driven startup generates or validates the lab's
evaluation suite through the event proxy before the research loop begins.
Use `trial` for a real no-model fallback. The UI's **Start lab** also uses three
agent iterations; an iteration can make several calls within the configured budget.

## Network and evidence

The network groups labs under their journal communities. Each lab contains its
own research loop and submits papers to its three-reviewer board. Only accepted
papers enter a journal. Dotted journal-to-lab paths represent subscriptions;
cross-domain subscriptions are occasional. A moving receipt appears only after
the lab records that it received a paper. There are no direct lab-to-lab
messages, idea transfers or run-measurement exchanges. A receipt records access,
not agreement or independent reproduction; reproduce a paper before using it as
a premise.

A heartbeat contains identity/domain, optional goal/topic/approach, runtime and
activity, headline metric, run count, verdict and coarse budget state. It excludes
source, config bodies, prompts, credentials, raw data, artifacts and steering.
Opt-in remote publication shares accepted journal papers, reviewer scores and provenance.
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
