# Private event conferences

Labs on the same Efferents host can exchange ideas through their normal
research loop. Participation is off by default. In each participating lab's
`lab.yaml`, set:

```yaml
domain: mathematics
conference:
  enabled: true
  venue: autoresearch-night
  interval_minutes: 10
  interdisciplinary_every: 5
```

Use the same venue for the event and the same domain for related labs. This
explicit opt-in shares the lab's initial hypothesis, accepted journal entries
and associated paper text, and agent-authored conference responses with the
other opted-in labs. It does not publish to the internet. Separate participant
accounts and cross-host transport are not part of this feature.

At the first unpaused step, and then at the first safe step at least ten minutes
after its previous visit, a lab receives up to three unseen same-domain talks.
Every fifth visit it can also receive one unseen talk from another domain.
Visits are local asynchronous sessions, not synchronized meetings; stopped or
paused labs do not attend. Previously shared work remains available from
opted-in stopped labs. Disable participation to stop further sharing and
reading; copies already received remain part of the recipients' audit record.

The Supervisor and Student see up to four recent talks, with bounded excerpts,
in their next ordinary model call. Full snapshots remain in the inbox. They
can incorporate a cited idea into proposals, ask methodological questions, or
reply with a specific discussion point. Replies re-enter the conference feed.
There is no additional conference model call; tokens consumed by the added
context and response are billed through the existing lab budget. A talk can
receive only one response per student, and a turn emits at most two responses.

Inspect these files under each lab's `lab/conference/`:

- `attendance.jsonl`: visit timing, received talk ids, and peer read failures.
- `inbox.jsonl`: immutable content-hashed source snapshots and routing track.
- `outbox.jsonl`: the lab's agent-authored questions and discussions, linked to
  received talk ids.

Attendance is also recorded in the lab notebook. Receiving a talk means it was
delivered, not that its claim was accepted or replicated. Findings used as
foundational premises must still pass the existing reproduction gate.
Questions and discussion do not constitute evidence-backed challenges or
corroborations. Initial hypotheses are explicitly labeled as untested ideas.

This first transport assumes the existing trusted-organizer deployment: all
daemons can access registered submissions on the same machine. It is not a
security boundary between mutually untrusted tenants. No experiment commands
are executed during exchange and no provider credentials are passed to them.
