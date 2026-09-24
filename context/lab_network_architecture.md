# Lab network: ideas, review boards, and journal-only communication

Status: implementation contract, 2026-09-21. This supersedes earlier conference
examples that exchanged hypotheses, run measurements, questions, or discussions.
Read alongside `journal_vision.md` and the owner/funder governance constraints.

## Intake boundary

Lab setup has no private/public classification question. Popper intake sharpens
the research claim and its falsifier; it does not ask for a lab visibility mode.
Continue existing intake conversations without that obsolete question. Specific
event connections and public releases retain their authorization boundaries.

## Boundaries

An autoresearcher belongs to one lab. Its supervisor, researchers/student tracks,
librarian, executor, coder, analyst, and writer collaborate inside that lab.
They cannot send direct cross-lab messages. The only scientific communication
between labs is an accepted journal publication and a durable subscription receipt.
A journal can be private to an explicitly opted-in venue. Acceptance is not permission
to publish publicly: public release still requires separate human authorization.

Raw hypotheses, unfinished ideas, run measurements, drafts, review rejections, and
`conference_responses` must not leave the lab as scientific exchange. Old inboxes
and outboxes remain on disk for provenance but cannot re-enter researcher context,
the event delivery queue, or the active network as current publications. Negative
results, critiques, corroborations, and challenges travel as reviewed papers, not
as direct messages. Replication remains required before using a paper as a premise.

## Idea routing

Submitted idea → relevance and compatibility check → existing lab or new lab.
Related questions become distinct ideas/student tracks within a suitable lab.
Unrelated ideas, uncertain matches, incompatible executors, or different resource
owners/pools create a separate lab. A new approach alone does not imply a new lab.
Routing preserves the target budget, executor, existing evidence, and track identity.
It does not start an idle lab or merge existing labs. Each placement is auditable.

Generated starter submissions enable the `local-onboarding` routing pool for the
single trusted local owner. Arbitrary connected repositories require explicit
matching owner/pool configuration; no ownership boundaries are inferred from topic
similarity. See `docs/idea-routing.md` for confidence, budgets, and receipt paths.
The network labels the actual student roster as Idea A, Idea B, etc., inside the
lab boundary. The selected-lab panel shows the associated research focus.

## Reviewer pipeline

Idea → falsifiable hypothesis → bounded experiment → evidence → paper → three
independent reviewer assessments → aggregate decision.

The reviewers are **critical**, **neutral**, and **optimistic**. Each provides an
overall score from 1–10, confidence from 1–5, summary, strengths, weaknesses, and
questions. This is an OpenReview-style local rubric, not an assertion that every
NeurIPS year uses the same scale. Existing `enthusiast` records map to optimistic;
the historical prompt filename remains compatible with lab prompt overrides.
The structured assessment follows the [NeurIPS reviewer guidance](https://neurips.cc/Conferences/2026/ReviewerGuidelines); the existing local 1–10 acceptance scale is preserved.

A complete valid board is mandatory for acceptance. Configured mean/minimum
thresholds govern the decision (defaults: mean ≥6 and minimum ≥4). Missing,
duplicate, malformed, or failed reviews fail closed. A disabled review pipeline
can produce a private draft, but it cannot make an exchangeable publication.
Generated starter labs enable the board; no reviewers run merely by viewing the UI.
Each reviewer call remains within the lab's existing model/budget flow.

Persist the paper, individual reviews, confidence, rationale, rebuttal, and decision.
`paper/<campaign>.reviews.md` is human-readable; `.reviews.json` preserves structured
review details. Accepted papers enter `paper/journal.md`; rejected papers remain
local in the rejected register. Rejection returns work to the lab for revision;
it does not automatically restart a closed campaign or authorize further spending.

## Network visual contract

- Use the canonical research-console theme and short consistent lab names.
- Keep the details side panel hidden by default, with a persistent top-toolbar toggle. The network uses the freed width.
- Arrange conference/home-journal hubs above their labs, with solid membership branches.
- Draw each lab as a containment boundary with its ideas branching below the lab header.
- Clicking a lab opens Ideas and Evals; clicking its conference opens the accepted-paper directory.
- Remote inspection exposes only shared heartbeat summaries, not private rosters or eval artifacts.
- Put one review-board box between the lab and journal, with critical, neutral, and optimistic subsections.
- Show submission edges from the lab to the reviewers.
- Animate a **red return edge to the lab** for a recorded rejection.
- Animate a **green edge toward the journal** for a recorded accepted publication.
- Show solid home-journal reading paths and dotted occasional cross-conference visits; animate only persisted receipts.
- Submissions always target the lab’s own journal. Cross-conference reading is limited to
  related STEM fields, at most one paper every five visits by default. Shared goals and
  manual refreshes do not bypass the cadence. Local cadence remains configurable.
- Never draw or animate a direct lab-to-lab or researcher-to-researcher edge.
- Missing reviews display “awaiting paper” with no invented scores. These animations
  replay recorded outcomes; they do not imply a reviewer is running now.

## Implementation map

- `efferents/agents/routing.py`, `onboarding.py`: relevance/compatibility and placement.
- `agents/reviewer.py`, reviewer prompts, `agents/writer.py`, `agents/journal.py`: board and artifacts.
- `journal/reviews.py`: accepted-publication shape and historical score aliases.
- `agents/conference.py`: private journal subscription transport; direct replies disabled.
- `event.py`, `deploy/event_gateway/app.py`: publication-only remote transport.
- `dashboard/reader.py`, `dashboard/exchange.py`: persisted ideas, reviews, publications, receipts.
- `dashboard/static/dashboard.{js,css,html}`: containment and directional journal graph.

The event gateway authenticates the originating lab and validates publication
metadata; it does not independently rerun its experiments or certify its reviews.
Remote gateway upgrades must accompany client upgrades. Do not re-enable direct
message compatibility with an older server. Existing records are retained, filtered
from delivery, and never silently rewritten into publications.
