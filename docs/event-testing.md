# Event rehearsal

Prepare a password-protected, localhost-only console with three real labs:

```bash
uv run python scripts/serve_event_test.py --background
```

Open `http://localhost:8840/#network`. The generated username, password and
restart instructions are in `event-output/tomorrow-test/TEST_ACCESS.md` (private,
ignored by Git). Keep the computer awake. After a reboot, rerun the command;
it preserves the password and evidence. This is not an Internet-accessible link.

## Five-minute test

1. Inspect the evacuation and mathematics labs. Read each lab's current
   hypothesis and evidence; measurements come from real bounded CPU runs.
2. Follow the network from each lab boundary to its review board and journal.
   Dotted journal-to-lab paths show subscriptions. A moving receipt appears
   only for a persisted paper delivery; it does not mean the recipient agrees
   or has reproduced the result.
3. Choose **Start an idea** and enter a mathematics question, such as adaptive
   Simpson integration near a narrow boundary layer. Review the displayed
   starter claim and scope notice before running. The starter's fixed
   integration experiment does not test arbitrary submitted mathematics; use
   **Connect a repository** for an executable custom experiment.
4. Run the bounded CPU trial and inspect the actual claim, config, metrics,
   verdict and provenance. A negative result is evidence, not a broken run.
5. Confirm the board has critical, neutral and optimistic assessments before
   treating a paper as accepted. Three evacuation seeds are preliminary; its
   claim requires twelve distinct seeds.

## What is implemented

Ideas route to a suitable lab and research track, or to a separate lab when the
question is unrelated or incompatible. Raw ideas, drafts, run measurements,
rejected papers and replies remain inside their originating lab. Reviewed,
accepted papers and persisted subscription receipts are the inter-lab record.
Cross-domain journal subscriptions are occasional. Receipt arrows record access,
not scientific agreement; reproduce a paper before relying on it.

New starters use lightweight claim / measurement / stop-condition contracts;
Popper Probe is not needed. Existing Popper-based labs remain supported. Inference
chooses among actual executable starters, not an invented experiment for every
possible idea. The selected starter scope is shown before its trial. Connect an
existing repository for other domains.

## Model and remote limitations

The rehearsal strips inherited model credentials by default: no model spending
is needed. To deliberately test model research, restart with `--enable-models`
after configuring a funded provider or joining a configured private event proxy.
The UI authorizes at most three agent iterations per start. An iteration can
include multiple model calls, constrained by the lab's monetary limits.
Credit/auth/quota failures halt the bounded run with an auditable reason.

The available Anthropic account rejected the live check for insufficient credit.
No live Azure upstream, public hosting, or two-laptop remote rehearsal has been
verified. Gateway protocol, enrollment, quotas, consent and receipts have automated
tests; those do not substitute for a real cloud rehearsal. See the
[operator runbook](event-operator-runbook.md) before inviting remote participants.

Owner steering and pause/stop remain available. Inference does not authorize public
publication, remove budgets, rewrite evidence, or grant approval for arbitrary code.
