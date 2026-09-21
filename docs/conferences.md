# Private journal subscriptions

Labs exchange accepted journal papers only. Autoresearchers remain inside their
own labs; raw measurements, ideas and direct questions/discussions are not a
cross-lab transport. See the [network architecture contract](../context/lab_network_architecture.md).

Opt in using the existing compatibility configuration:

```yaml
conference:
  enabled: true
  venue: private-event
  interval_minutes: 2
  interdisciplinary_every: 3
peer_review:
  enabled: true
  accept_mean_threshold: 6.0
  accept_min_threshold: 4
```

The daemon reads up to three related journal publications at a safe research-cycle
boundary and one cross-field publication every configured number of visits.
Only papers recorded in an accepted journal with all three reviewer scores are
eligible. **Read journal papers** requests a local subscription refresh. Reading
papers does not authorize public release or certify independent replication.

Critical, neutral and optimistic reviewers score each submitted paper. The network
shows red rejection returns to its originating lab and green accepted-publication
paths into its journal. Journal subscription receipts travel back to readers.
Ideas remain inside their labs; a relevant new idea may become another student
track through the [intake router](idea-routing.md).

`lab/conference/inbox.jsonl` and `attendance.jsonl` are append-only receipt and
visit ledgers. Historical outbox records remain available for audit; no direct
responses are emitted or relayed. Old non-publication inbox entries are excluded
from researcher prompts and the network. Accepted review and paper artifacts live
under `paper/`; rejected submissions stay local.
