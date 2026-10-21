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

For a participant lab with a reviewed run command, opt in under `executor` in
`lab.yaml`:

```yaml
executor:
  isolated_parallel_safe: true
```

The run command should use `{config_path}` for the generated config and keep
source and output paths inside the supplied source workspace. Do not opt in if
the command writes to fixed absolute paths, shares mutable process-wide files,
or depends on concurrent runs editing the original checkout. With multiple
ideas, the default is two workers; `EFFERENTS_MAX_PARALLEL_IDEAS=1` forces
serial execution, and values up to 16 set a higher local limit after resource
review. Single-idea labs use one worker.

The hub's authenticated connection configuration serves the tested participant
wheel. Updating the hub changes the wheel offered to new connections; existing
participant installations need to stop their local daemon, reinstall that
SHA-pinned wheel, and restart in the same submission folder. Their state,
queues, evidence, and billing history stay in place.
