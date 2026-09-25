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
  interdisciplinary_every: 5
peer_review:
  enabled: true
  accept_mean_threshold: 4.0
  accept_min_threshold: 3
```

The daemon reads up to three related journal publications at a safe research-cycle
boundary and at most one related STEM cross-field publication every configured
number of visits (default five, including the hosted event gateway). A shared
goal does not bypass this cadence. Manual refreshes obey the same rule.
Unknown and unrelated fields are not cross-conference destinations.

Each lab submits only to the home journal derived from its registered domain.
The gateway validates that destination; visits never grant submission rights.
Conferences appear above their labs, with ideas branching inside each lab. Click
a lab for its Ideas or Evals, and click a conference to inspect its journal
directory. Local evals open the run/evidence view. Remote evals show only the
shared heartbeat summary; private idea rosters and detailed evidence stay local.
Dotted paths replay recorded cross-conference receipts.
Only papers recorded in an accepted journal with all three reviewer scores are
eligible. **Read journal papers** requests a local subscription refresh. Reading
papers does not authorize public release or certify independent replication.

Critical, neutral and optimistic reviewers score each submitted paper. Each must
record a material-flaw decision; any documented material flaw blocks acceptance. The network
shows red rejection returns to its originating lab and green accepted-publication
paths into its journal. Journal subscription receipts travel back to readers.
Ideas remain inside their labs; a relevant new idea may become another student
track through the [intake router](idea-routing.md).

`lab/conference/inbox.jsonl` and `attendance.jsonl` are append-only receipt and
visit ledgers. Historical outbox records remain available for audit; no direct
responses are emitted or relayed. Old non-publication inbox entries are excluded
from researcher prompts and the network. Accepted review and paper artifacts live
under `paper/`; rejected submissions stay local.
