# Route related ideas into student tracks

The intake router groups related scientific questions into a shared lab, while
keeping different approaches as distinct students. This differs from the older
`efferents place` helper, which looks for duplicate topics AND approaches.

Enable routing in each incoming and receiving submission's `lab.yaml`:

```yaml
topic: mathematical reasoning effectiveness and cost
approach: compare explicit reasoning with repeated direct answers
routing:
  pool: autoresearch-night
  owner: organizer
  accept_students: true
```

Use the same pool and resource owner for labs that may share resources.
These labels are trusted organizer configuration, not participant authentication.
Set `accept_students: false` on a lab that should not receive new tracks.
Without routing configuration, connection keeps its existing separate-lab behavior.

The Connect flow invokes the router automatically for fresh submissions. The
router uses one bounded model call when credentials are available, comparing
hypotheses, topics and approaches across at most twelve eligible labs. It
joins only for a confidence of at least 0.8 and a valid candidate. Without a
key, it uses conservative explicit-topic overlap; the decision records which
method was used. Invalid model responses or provider failures stop connection
instead of silently applying an uncertain placement.

Preview or apply from the command line:

```bash
efferents route ./submission
efferents route ./submission --apply --student-id participant-b
efferents route ./submission --offline
```

A join requires the same owner/pool and compatible source bytes, executor
commands, provider environment passthrough, config shape and headline metric.
Different experiment implementations stay separate until they have a shared
runner. A different approach alone does not force a separate lab. Routing
never imports the incoming executor or budget into the destination.

On join, the original primary student stays in place. The newcomer gets a
separate student id, focus, initial campaign, and a hashed snapshot of their
passed hypothesis. Its campaigns and runs use existing student attribution;
the students share the lab's research context, evidence and spending cap. The
daemon picks up the roster at its next unpaused iteration. A stopped lab stays
stopped. An already-running lab may work on the new track at that boundary.
Existing registered labs and prior experiments are not merged.

Provider keys are loaded only in a separate routing process from the incoming
submission's `.env`. The web server never loads them. Model selection uses
`EFFERENTS_MODEL_ROUTER`, falling back to `EFFERENTS_MODEL` and then GLM-5.3.
The routing budget is separate from research: $1 per UTC day and $5 lifetime
for the host, recorded in `$EFFERENTS_HOME/routing/costs.jsonl`. Connecting an
opted-in submission therefore can spend routing tokens, but does not start
an experiment. Offline mode spends none.

Routing decisions and reasons are appended to
`$EFFERENTS_HOME/routing/decisions.jsonl`. Applied receipts make repeated
connection idempotent. Original hypothesis snapshots live under the destination
`lab/intake/`; the charter records the incoming direction. Previously routed
ideas cannot be changed in place: submit a new idea directory for a new claim.
