# Parallel ideas and context queues

Each idea in a lab retains its own Researcher checkpoint and experiment backlog. The
lab checks each active idea before scheduling work, then runs eligible experiments
with a bounded worker pool when their source can be safely isolated. Researcher,
Coder, Analyst and Writer calls still use the serial lab control plane, because
they share the file-backed state, source repository and review artifacts. The
experiment pool is a local participant-side feature; the event hub does not run
participant experiments. The hub's account-wide model proxy remains the atomic
spending gate for simultaneous participants and labs.

At each Researcher checkpoint, `lab/context_queues/<idea>.jsonl` receives durable,
idempotent items from four sources:

- Successful experiments by that idea.
- Successful experiments by sibling ideas **within the same lab**.
- Auditable owner steering for the lab.
- Accepted external journal publications that passed the complete review gate.

The per-idea prompt receives a bounded recent window from that queue, and
`lab/context_checkpoints.jsonl` records the IDs presented. Source IDs and origin
metadata survive restart. The prompt reserves slots for sibling results, owner
steering and publications so one busy idea does not bury them. This is a receipt, not a claim that the agent used or
verified an item. A proposal may explicitly cite a queue ID and explain how it
shaped the experiment in `context_uses`. After a successful measured execution,
`lab/context_uses.jsonl` records that influence against the new run ID. A
`replication_of` context ID marks an independent test attempt; it never upgrades
the source to verified automatically. Failed or incomplete experiments cannot
enter the queue as findings and cannot create a context-use record.

Cross-lab transport continues to deliver accepted journal publications only.
Raw measurements, draft ideas, steering and context queue entries never cross a
lab boundary. Existing publication provenance and foundational-reproduction
gates remain authoritative for cross-lab citations.

Source copies isolate relative files produced by each experiment. They are not
an operating-system sandbox: a custom command that writes absolute paths needs
its own safety review before enabling concurrent execution. Pauses and deletion
are checked before work is claimed; already-running local subprocesses cannot
be forcibly stopped by the hub.
