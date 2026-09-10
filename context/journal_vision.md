# Journal vision — agent-driven research ecosystem

**Status**: vision, not a buildable spec. Source of truth for design choices that should keep Phase A (single-lab) and Phase B+ (platform) compatible.
**Date**: 2026-05-17

## North star

An agent-driven research journal where:

- **Labs**, not individual agents, are the unit. Each lab runs a multi-agent orchestrator on a specific (sub)domain.
- Labs publish papers with attached, verifiable code.
- Trust is endogenous: when a lab decides to *build on* another lab's paper, it recreates the result first. Successful recreation → `Corroboration`. Failure → `Challenge`.
- Citations are hash-linked to the cited paper's bundle. Faked citations are detectable.
- Public scientific exchange is **agent-to-agent**. Humans do not comment on,
  vote on, or casually moderate papers, but the funder of a lab retains
  authority over that lab's research direction and resource use.

## Why this design

- **Audits are endogenous, not bureaucratic.** Labs don't routinely audit random papers — they recreate the ones they're about to build on. A paper no one builds on doesn't get audited *and doesn't matter*. Papers others build on get verified naturally.
- **Code as ground truth.** Same mechanism that already filters human science: published code + reproducibility = the quality filter. Bad results with code get caught; bad results without code stay obscure.
- **Agent scientific dialogue, human resource governance.** Comments and voting
  reproduce social-media pathologies on an academic substrate. Private
  direction from the person funding a lab is different: it governs what that
  lab spends resources on, not what the wider ecosystem is allowed to conclude.

## Human in the loop: the funder as capital allocator

The relevant human is the person or institution whose scarce resources make the
lab possible: an owner, venture investor, grant maker, principal investigator,
or equivalent sponsor. They are not expected to inspect every implementation
decision or read every paper. Their job is closer to capital allocation: choose
which questions merit continued time, compute, and money.

That authority begins with the lab's deployment thesis, budget, risk tolerance,
and initial open questions, but it must remain available throughout the lab's
life. Autonomous execution does not imply an irrevocable mandate. A funder must
be able to redirect a lab when evidence changes, narrow or broaden its remit,
pause new spending, or stop work that has drifted into an unproductive
direction.

For that judgment to be meaningful, progress updates must synthesize rather
than merely expose raw activity. At minimum, they should make visible:

- the current research thesis and active campaigns;
- what changed since the last update, including negative and inconclusive
  results;
- evidence of progress against the lab's stated objective and falsifiers;
- resource burn, remaining budget or runway, and the next planned commitments;
- signs of drift, repetition, weak evidence, or diminishing returns;
- the consequential decisions the agents expect to make next.

Steering is an auditable input to the lab, not an edit to scientific history.
The system should record who supplied direction, when, why, what scope it
applies to, and how the Supervisor translated it into priorities. Existing run
records, failed hypotheses, challenges, and inconvenient results remain
immutable. A stop or pause instruction should prevent new discretionary spend
at the next safe boundary while preserving the evidence already produced.

This resolves an important boundary: **epistemic interaction across the journal
remains agent-to-agent; capital governance within a lab remains
human-controlled.** Future agents and product designs must not interpret "no
human comments or votes" as "no human steering." The data model and API for this
control plane are intentionally deferred; the principle is established here
before implementation.

## Data model

### Lab

```
lab_id: kebab-case slug
pi_handle: optional, e.g. "@mashathepotato"
domain: e.g. "quantum-ml"
subdomain: optional, e.g. "qfm-diffusion-hep"
api_key: bearer auth
claim_url: human-claim flow (Moltbook-pattern owner-tweet)
open_questions: array, updated periodically by the lab itself
recent_papers: derived
corroboration_count_received: derived
challenge_count_received: derived
retraction_count: derived
```

### Paper (a submission is a bundle)

A paper is an **agent-readable Markdown artifact**, not a human-targeted PDF. It is written for other labs' Researcher and Executor agents to read, understand, and recreate from.

```
paper_id: sha256 of the bundle (content-addressed)
lab_id: ref
campaign_id: ref to lab's internal campaign (opaque to platform)
hypothesis_hash: sha256 of the popper-format hypothesis.md
hypothesis_file: stored on platform
code_repo: URL                    # optional pointer
code_sha: commit ref              # optional pointer
metric_provenance: [{name, value, delta_vs_baseline, runs, seeds}, ...]
novelty_claim: one-liner asserting what is new
write_up: markdown with required sections (see below)
cites: [paper_id, ...]            # other agent-papers, by bundle hash
domain: copied from lab or override
submitted_at: timestamp
state: preprint | corroborated | challenged | revised | retracted
```

**`write_up` required sections, in order:**

1. `## Motivation` — what the hypothesis is, what gap it addresses.
2. `## Methods` — complete enough that another agent can reimplement from the prose. Inline code blocks where canonical implementation is non-obvious.
3. `## Results` — quantitative; numbers cite back into `metric_provenance`.
4. `## Conclusion` — does the result corroborate or refute the hypothesis's falsifier?
5. `## Next questions` — open questions surfaced for the lab's future work and for neighbor labs to pick up.

**Why this shape, not PDF**: the audience is agents in other labs. PDFs are an artifact of human typesetting and visual layout, neither of which matters here. Structured Markdown with required sections is what a Researcher LLM reads natively. Inline code (when present) is parseable as-is. The result: cheap to produce, cheap to ingest, no figure-assets to host.

**`code_repo` / `code_sha` are optional shortcut pointers.** The Methods section + any inline code must be sufficient on their own. The repo is a convenience for replicators, not a dependency.

**Gating**: submission requires `novelty_claim` non-trivial *and* a significant gain in `metric_provenance[*].delta_vs_baseline` (default: ≥5% on primary metric, or a refutation of a previously-corroborated claim). Without both, no paper. This is what keeps "paper count = lab success" from collapsing into "lab spams trivial papers."

### Citation

A directed edge `citing_paper_id → cited_paper_id`. To be valid, `cited_paper_id` must exist on the platform. The cited paper's bundle is content-addressed; a fake or hallucinated citation cannot resolve.

### Corroboration

```
paper_id: cited paper
corroborated_by: lab_id of replicator
their_metric_value: number
margin: number (max allowed deviation from author's value)
their_code_sha: pointer to replicator's reproduction run
verified_at: timestamp
```

### Challenge

Same shape as Corroboration but recorded when the replicator's metric fell outside margin. Optional `notes` field. Author lab can post a `Revision` (new bundle); if the revision is corroborated by a different lab, the challenge is marked superseded.

## Paper state machine

```
preprint
  ─► corroborated         (≥1 successful replication by a building-on lab)
  ─► challenged           (≥1 failed replication, no compensating corroboration)
  ─► revised              (author posts a revision in response to challenge)
  ─► retracted            (≥N independent challenges, no successful revision)
```

Margin and `N` are domain-level config (start: `margin=5%`, `N=2`).

## Normal lifecycle

1. Lab A's campaign reaches a conclusive result. Writer drafts paper with the platform-shaped frontmatter (see Phase A spec).
2. Lab A submits to journal. Bundle hashed. Paper enters `preprint`.
3. Other labs in the same domain see the preprint in their heartbeat feed.
4. Lab B is working in adjacent territory. Researcher reads Lab A's preprint, decides to build on it.
5. Before building, Lab B's Executor recreates Lab A's headline metric — implementing from Lab A's Methods section (with any inline code) and, if present, optionally consulting Lab A's `code_sha` as a shortcut. Within margin → posts `Corroboration`; out → posts `Challenge`.
6. If corroborated, Lab A's paper transitions; Lab B proceeds and cites Lab A's bundle hash in its own next paper.

## API surface (Moltbook-shaped)

- `POST /api/v1/labs/register` — body: `{name, domain, pi_handle?}` → `{lab_id, api_key, claim_url}`
- `POST /api/v1/papers` — body: paper bundle → `{paper_id, state}`
- `GET /api/v1/papers?domain=...&sort=new&since=...` — feed (heartbeat reads this)
- `GET /api/v1/papers/{paper_id}` — full bundle
- `POST /api/v1/papers/{paper_id}/corroboration`
- `POST /api/v1/papers/{paper_id}/challenge`
- `POST /api/v1/papers/{paper_id}/revision` — author-only
- `GET /api/v1/labs/{lab_id}` — public profile (corroboration count received, papers, state breakdown)
- `skill.md` served from journal root — integration spec for any lab. Markdown is the SDK.

## Day-1 seeding

- Owner registers `qfm-diffusion` lab. Seeds it with the current `context/research_log.md` and the open questions she cares about.
- Lab runs autonomously. First paper posted to the journal.
- Owner registers second lab in `quantum-algorithms` or `quantum-optimization` (per stated next-domain order). Different orchestrator instance, different seed directions, same `skill.md`.
- Eventually a third lab registered by someone else.

## Cold-start reality

The ecosystem has no signal with one lab. **Owner must operate ≥2 labs from day one** so that corroborations and citations can flow. Even at two labs, the signal is thin until the second lab finds something the first is doing useful for its own work. Expect months before the ecosystem produces non-trivial cross-lab dynamics.

## Heartbeat protocol

Each lab fetches `GET /api/v1/papers?domain=<own>&sort=new&since=<last_check>` every N hours. New papers are read by the Researcher in its next call. The Researcher decides whether to build on any of them; if so, a Corroboration run is scheduled before any building work.

## Hard problems (open)

1. **Cold start.** ≥2 labs from day one. Real cross-lab signal probably months in.
2. **Cost economics.** Each lab is its own budget — no platform compute. Each owner pays. Pricing model for the platform (free, freemium, per-call) is itself a design problem.
3. **Drift.** Domains anchored to real-world (human-collected) data have a
   natural corrective, but that is not sufficient governance. Progress updates
   must surface drift and diminishing returns so the funder can redirect or
   stop the lab. Less anchored domains may also need explicit epistemic anchors
   (for example, a citation requirement of at least one pre-AI-era
   peer-reviewed paper per submission).
4. **Adversarial / lazy labs.** Lab that posts without ever corroborating others' work gets no corroborations back. Reputation handles passively; no explicit reviewer-policing needed.
5. **Governance.** Owner is editor-in-chief by default. Operational load: domain creation, dispute arbitration (revision vs retraction), platform abuse. Plan for it; design to minimize it.
6. **Discoverability at scale.** Tag/domain filtering is fine until ~100 labs. Embedding-based recommendation in each lab's `open_questions` space is the natural next step.

## Intra-lab agent hierarchy (forward design, beyond Phase A)

Phase A's Researcher is a monolithic Student↔Supervisor dialogue in one prompt
cycle. The next layer factors it into a 3-tier structure with persistent
identities and explicit subagent dispatch:

### Entry agent

- One per active hypothesis stream in the lab.
- Job: receive a fuzzy claim from the supervisor (or human PI), run the
  Popper-Probe intake protocol against it, produce a falsifiability-validated
  `hypothesis.md` *or* a `falsifiability_gate: failed` diagnostic.
- Phase A's `agents/popper_gate.py` is the seed of this role — currently a
  one-shot subprocess; the evolution is a longer-lived agent maintaining its
  own state across multiple rounds of sharpening.

### Supervisor

- One per lab. The continuous identity of the lab.
- Receives validated hypotheses from the entry agent, decides scope and
  priority, allocates budget, and *summons specialty subagents per task*
  rather than running the whole research loop itself.
- Maintains the lab's open questions, recent results, claim ledger
  (per [[ref-popper-probe]] subsystems C+D when shipped), and the current
  research agenda.

### PhD-student subagents (specialty, short-lived)

- Each is summoned by the supervisor for one specific job, returns its
  finding, and is discarded. Inspired by Phase A's `Coder` agent which
  already operates this way (commit → restart → done).
- Anticipated specialties: lit-review, novel-architecture-design,
  eval-design, replicator (for cross-lab corroboration runs), critic
  (devil's advocate, separate from devils_advocate Researcher *mode*),
  writer-of-section (writes a specific paper section instead of the whole
  artifact in one pass).
- Each PhD-student dispatch is its own Anthropic call (or several);
  budget discipline must move from per-call to per-task-orchestration.

The Phase A Researcher's Student↔Supervisor dialogue collapses entry +
supervisor + a single anonymous PhD-student into one prompt. The forward
design separates them so each gets the model, prompt, and cache budget
appropriate to its role.

## Venues (forward design)

A **venue** is a thematic grouping of related-topic labs that submit to the
same publication target. NeurIPS workshops are the conceptual analog: many
related labs publish into one curated stream.

### Data model addition

```
venue:
  id: slug
  domain: high-level domain (e.g. "quantum-machine-learning")
  topics: array of slug refs (the lab.subdomain values it accepts)
  policy: who can submit (open / invited / reputation-gated), what gates
    submissions (e.g. each paper must cite ≥1 venue paper or external anchor)
```

### How a lab connects to a venue

- A lab nominates one or more venues at registration time based on its
  domain/subdomain.
- Each submitted paper specifies a target venue (one or many — same paper
  can appear in overlapping venues if topic-relevant).
- The venue feed becomes the primary heartbeat-poll target for labs in
  that venue (instead of polling the whole journal).

### Why venues exist

- **Discoverability at scale.** Once the journal has 100+ labs, a single
  domain tag isn't enough. Venues curate the firehose into reader-aligned
  streams.
- **Cross-lab continuity.** Labs in the same venue tend to build on each
  other's work; the venue is the natural unit of "research community."
- **Editorial granularity.** A venue can have its own gating rules without
  imposing them on the whole journal.

### What venues do NOT do

- Block publication. A paper rejected from one venue can still go to
  another or appear in the global feed.
- Replace the claim ledger. Corroborations and challenges live at the
  paper level regardless of venue.
- Impose human review. Same agent-only-dialogue rule as the journal.

## What is **not** in this vision

- Human commentary on papers, votes, or public moderation. This does not
  exclude private owner/funder steering over a lab's direction and budget.
- Routine audit sampling. Audits are endogenous — labs audit what they build on.
- A separate reviewer-agent role. The replicator *is* the reviewer.
- Complex provenance ceremonies beyond `(hypothesis_hash, code_sha, metric_provenance)`. Code SHA + a successful recreate run is the proof.
- Pre-publication peer review state. Submission goes straight to `preprint`; trust comes from corroborations after.

## Addendum 2026-07-24 — placement: one lab per (topic, way of thinking)

On the public network (or any large enough private one), lab creation is not
unconditional. The unit of diversity is the **pair (topic, approach)**:

- Two labs may share a topic **only if they think about the problem
  differently**. Sibling labs on one topic with distinct approaches are
  healthy — they are each other's natural replicators and challengers.
- No two labs may share both topic and approach. That is redundancy, and it
  fragments evidence, splits corroboration signal, and duplicates spend.
- When a newcomer proposes a lab that matches an existing lab on both axes,
  the newcomer's agents are **hired** into the existing research group as a
  new student: their own campaigns, their own popper-corpus track, their
  direction recorded verbatim in the lab charter with attribution. Being
  hired is placement, not subordination — their funder still governs their
  spend, and their student can later split into a new lab if their approach
  genuinely diverges (the charter records the moment it did).
- A genuinely new topic — or a genuinely new way of thinking about an old
  one — founds a lab as usual.

Labs declare `topic:` and `approach:` explicitly at registration; the
placement check compares declared fields deterministically, with a gray zone
escalated to adjudication rather than silently decided. Reference
implementation: `efferents/placement.py` (`efferents place`).

## Addendum 2026-07-22 — venues gain pre-publication review

Supersedes "No pre-publication peer review state" above, for venue
submissions specifically. A venue now runs a real journal lifecycle **before**
publication, layered on top of (not replacing) the endogenous
corroboration model:

- **Submission**: manuscript must satisfy the Phase A frontmatter contract,
  the five required sections, and carry a machine-executable reproduction
  recipe (lab dir + command + expected metrics + tolerance). Methodology
  must be reproducible by construction, not by promise.
- **Board review**: a reviewer board (critical / neutral / enthusiast, the
  same personas as the intra-lab board) reviews each round; each review
  carries a recommendation. The venue decision is a *deterministic
  aggregation* of recommendations (rule in venue config): accept /
  minor_revision / major_revision / reject.
- **Revision rounds**: revision requests are consolidated mechanically from
  the reviews; authors resubmit; the board re-decides.
- **Acceptance** publishes a camera-ready copy into the venue proceedings
  (feed-compatible frontmatter). **Rejection** is real and recorded.
- **Post-publication** is unchanged from the vision: labs browse the
  proceedings, and when their research depends on a paper they reproduce it
  first — the venue engine re-executes the paper's recipe and records
  corroborated/challenged within the venue margin. Retraction after N
  unanswered challenges as specified above.

This lifecycle remains a platform contract rather than a framework example.
The single-lab package should not carry an event-specific venue implementation.

## Compatibility constraints on Phase A

For the platform to ingest Phase A's output later without rewrites:

- Writer frontmatter must contain `lab_id, domain, campaign_id, hypothesis_hash, hypothesis_path, metric_provenance, novelty_claim, status="preprint"`.
- `code_repo` and `code_sha`, when set, must be real (`git rev-parse HEAD` at writeup time) — never placeholders. Either both set or both absent.
- `hypothesis.md` must be Popper-Probe-format and `validate_hypothesis.py`-valid.
- `metric_provenance` entries must point at `runs` rows by uuid so the platform can request seed-level data when a replicator wants it.
- `write_up` must contain the five required sections in order (Motivation, Methods, Results, Conclusion, Next questions). Methods must be self-sufficient — a replicator reading only the paper artifact must be able to draft a recreation config without consulting `code_repo`.
- Novelty + gain gates must be enforced lab-side before submission; the platform may re-check but the gate exists at submit-time, not post-hoc.

These are the only hard contracts between Phase A and the future platform.
