# Autoresearch Night readiness and starter-lab plan

**Status:** planning only; no implementation is authorized by this document  
**Written:** 2026-09-15  
**Candidate event dates:** Tuesday 2026-09-22 or Thursday 2026-09-24

This is the handoff for a later implementation session. It records the event
decisions already made, turns every readiness item into either a completed
decision or a concrete task, and scopes the remaining code around one useful
starter lab.

## Fixed event architecture

- [x] **Labs execute on participant machines.** Each participant creates or
  clones a repository and keeps its experiment code, raw data, run ledger and
  evidence locally.
- [x] **A coding agent is the primary setup path.** The participant opens an
  agent in the repository and gives it the Efferents intake instructions.
- [x] **ChatGPT web is an assisted setup path.** It can help form the
  hypothesis and generate the lab contract. Unless it has a connected coding
  environment, the participant still runs the displayed local commands.
- [x] **The event server has only two event responsibilities:** broker model
  access and collect the small, explicitly shared summaries needed for the
  projected network view.
- [x] **The provider credential stays on the server.** Participants receive a
  revocable, expiring event proxy token. They do not receive the underlying
  Z.ai, Anthropic or other vendor key. This preserves the framework rule that
  provider credentials remain daemon-only and do not reach experiment
  commands.
- [x] **The organizer operates the projected network.** Participant access to
  organizer start, stop and steering controls is not needed for the minimum
  event. Each participant owns those controls locally for their own lab.
- [x] **Sharing is private and event-scoped.** Joining the event network is an
  explicit opt-in. It does not publish a lab or its artifacts publicly.

The existing event-hosted instructions in `intake.md` describe a different
architecture: participants prepare locally, then the organizer clones and runs
their labs on the hosted machine. Those instructions must be replaced before
they are used at this event.

## Bare-minimum readiness checklist

### 1. Where labs run

- [x] Participant laptops are the execution hosts.
- [x] The server brokers model access and displays the network.
- [ ] Document the two supported setup lanes: coding-agent setup and ChatGPT
  web-assisted setup.
- [ ] Verify the participant machine assumptions: macOS/Linux, Python 3.10+,
  Git, `uv`, outbound HTTPS and permission to install a package. Decide whether
  Windows is supported at this event; do not imply it was rehearsed if it was
  not.

**Done when:** a participant can tell where every process and every category of
data lives, and the server never needs their repository path or source tree.

### 2. One reliable starter experiment

- [ ] Implement the recommended **congestion-aware evacuation lab** specified
  below.
- [ ] Make its baseline deterministic, its hypothesis Popper-passed and its
  first useful result finish in under 30 seconds on an ordinary laptop.
- [ ] Provide several independent research approaches without prescribing an
  answer, so participants can form distinct labs around the same simulator.
- [ ] Test the full autonomous cycle with the event model proxy and measure
  wall time and model cost. Set the default run and spend bounds from this
  measurement.

**Done when:** a clean laptop can produce a real, non-canned comparison, a run
ledger entry and one visual artifact without downloading data or using a GPU.

### 3. Complete attendee journey

- [ ] Rehearse this exact sequence from a clean temporary directory:
  1. Open the starter URL or create a new repository.
  2. Run the intake prompt in a coding agent, or follow the ChatGPT web lane.
  3. Choose one starter research direction.
  4. Pass the hypothesis through Popper Probe.
  5. Review `lab.yaml`, commands, edit scope and budget.
  6. Join the private event with an event token.
  7. Run `efferents validate --submission .`.
  8. Run one bounded local cycle.
  9. Inspect the hypothesis, evidence, verdict and budget locally.
  10. Send a sanitized heartbeat to the event network.
  11. Steer or stop the local lab.
- [ ] Record time-to-first-run, every manual decision and every failure. Fix
  blockers; put recoverable mistakes in the troubleshooting sheet.
- [ ] Repeat once with the coding-agent lane and once with the ChatGPT web lane.

**Done when:** both lanes complete without the organizer editing a participant
repository or copying it onto the server.

### 4. Model access and spending limits

- [ ] Deploy an OpenAI-compatible model proxy beside the event server. Store
  the provider key only in the proxy service environment.
- [ ] Issue a separate opaque event token per participant or lab. Tokens must
  expire after the event and be individually revocable.
- [ ] Enforce a server-side per-token allowance and total event ceiling. Keep
  each lab's existing `budget.daily_cap_usd` and `budget.total_cap_usd` as a
  second, local boundary.
- [ ] Make the local client work with the proxy through a generic API base and
  event token. Store the token in a mode-600 ignored credential file or `.env`;
  never write it to `lab.yaml`, a prompt, Git or experiment subprocesses.
- [ ] Add an organizer view of token status and aggregate spend without showing
  token values.
- [ ] Test valid, expired, revoked, exhausted and rate-limited tokens, plus
  recovery from a brief proxy outage.
- [ ] Set a provider-side hard spending limit as the final backstop.

**Done when:** revoking one participant token stops only that token, a lab halt
is recorded clearly, and no request or log exposes the vendor credential.

### 5. Lab-to-lab exchange

- [x] **Minimum event scope:** the shared network may show lab presence and
  progress without implementing live scientific exchange.
- [ ] Rehearse two remote laptop labs registering and updating the server
  network. The current `efferents.agents.conference` implementation reads other
  labs from the same filesystem and does not satisfy this check.
- [ ] Label the projected view accurately. If talks and findings are not synced,
  show labs and status only; do not present the existing illustrative replay as
  live activity.
- [ ] Treat live questions, replies and finding exchange as the first optional
  upgrade after the network heartbeat is reliable.

**Done when:** two separate machines appear and update on the network without
sharing source, raw data, prompts or full evidence.

### 6. One-page joining instructions

- [ ] Rewrite the event section of `intake.md` for local execution and proxy
  access.
- [ ] Add a short `docs/event-quickstart.md` containing prerequisites, the
  starter link, the copyable intake prompt, event join steps, expected timing,
  local owner controls, the exact fields shared with the network and the stop
  procedure.
- [ ] Add a compact ChatGPT web variant that produces files for a local repo and
  never claims the browser can execute them by itself.
- [ ] Link the troubleshooting page and offline fallback.
- [ ] Verify every command by pasting it from the rendered page into a clean
  environment.

**Done when:** the page fits a first-time participant's path and contains no
organizer-only deployment steps or obsolete hosted-run instructions.

### 7. Full rehearsal

- [ ] Use the actual venue network, projector, server hostname and event model.
- [ ] Rehearse one organizer and at least two participant laptops.
- [ ] Start from empty repositories and expired local caches where practical.
- [ ] Measure install time, first model response, experiment runtime, network
  update delay and cost.
- [ ] Test one deliberate failure: disconnect a laptop, revoke its token, then
  confirm local evidence remains intact and the network marks it stale.
- [ ] Freeze features after a passing rehearsal. Only fix event blockers.

**Done when:** the core journey completes twice and the fallback can be opened
without network or model access.

### 8. Offline fallback

- [x] A generic offline demo already exists via `efferents demo smoke-lab`.
- [ ] Generate and save one completed evacuation-lab result bundle with its
  dashboard, run ledger and artifacts.
- [ ] Package the starter repository and framework wheel/source needed to run
  the deterministic experiment without model access.
- [ ] Put the fallback on the organizer laptop and a USB drive; open it with
  Wi-Fi disabled.
- [ ] Prepare a short fallback facilitation path: inspect the saved evidence,
  propose the next experiment manually, run it locally and compare results.

**Done when:** the research exercise remains meaningful during a total Wi-Fi or
provider outage.

### 9. Event close

- [ ] Put a visible five-minute close in the run of show.
- [ ] Have participants stop their daemon, check `efferents status`, and locate
  `lab/runs.sqlite`, `lab/lab_notebook.md` and `lab/artifacts/`.
- [ ] Send one final network heartbeat with status `stopped` and then revoke or
  expire event proxy tokens.
- [ ] Export the event network summary before shutting down the server.
- [ ] Prepare the follow-up link: repository, quickstart, feedback form and how
  to continue locally with the participant's own provider access.
- [ ] Record retention: delete event tokens immediately; retain only the
  consented network summaries for the stated period.

**Done when:** no lab is unknowingly spending, participants retain their local
work, and continued access does not depend on the event credential.

## Expanded nice-to-haves

Every item below is outside the minimum unless promoted after a passing full
rehearsal.

### A richer research experience

- [ ] **Several domains:** after the evacuation lab works, add one language
  experiment and one natural-system simulation using the same lab contract.
- [ ] **Beginner and advanced paths:** beginner varies config and evaluates an
  approach; advanced enables Coder review mode and implements a new policy.
- [ ] **Preloaded labs:** bundle two completed evacuation approaches with honest
  labels as prior example evidence, not live event output.
- [ ] **Cross-lab question and replication:** provide a scripted exercise in
  which one lab publishes a bounded claim and another reruns the same seeded
  scenarios before building on it.
- [ ] **Live conference activity:** replace same-filesystem conference reads
  with a remote transport and render real talks, replies and finding links.
- [ ] **End-of-session summaries:** create a local export command and a central
  event summary containing only opted-in fields and evidence references.

### Easier participation

- [ ] **Pre-event setup session:** offer a 20-minute install/token check. This
  is scheduling, not framework code.
- [ ] **Second helper:** assign one person to installation and proxy problems
  while the organizer facilitates. This is staffing, not code.
- [ ] **Troubleshooting sheet:** consolidate current diagnostics for Python,
  `uv`, Git, Popper Probe, proxy auth, no credit, rate limits, ports and stale
  network status.
- [ ] **QR codes:** generate codes for the quickstart, starter repo and feedback
  form after their URLs are final.
- [ ] **Bring-your-own repository path:** rehearse `intake.md` on one small real
  repository and document the minimum executor contract. Keep it as an
  advanced lane during the event.

### Presentation and logistics

- [ ] **Opening deck:** explain local ownership, falsifiable hypotheses,
  bounded spend, evidence, steering and the event network in ten minutes.
- [ ] **Demo recording:** capture a two-minute starter run after the UI and
  quickstart are frozen.
- [ ] **Starter cards:** write one card per suggested approach with the question,
  measurable outcome and likely extension; do not reveal expected winners.
- [ ] **Physical supplies:** snacks, drinks, name tags and signage are already
  part of event planning and need no repository work.
- [ ] **Photography:** decide consent and a no-photo marker before recording.
- [ ] **Feedback/follow-up:** prepare the form and email, including permission
  before using quotes, screenshots or research summaries publicly.

### Operational polish

- [ ] **Concurrency test:** simulate the expected participant count through the
  model proxy and heartbeat API. Measure latency, errors and spend rather than
  extrapolating from one lab.
- [ ] **Backup/recovery:** back up the server's event registry and proxy quota
  database, restore them on a clean instance, and verify the projected network.
  Participant evidence remains each participant's responsibility; add a local
  export reminder.
- [ ] **Reusable runbook:** record setup, token issuance, health checks,
  rehearsal, incident responses, closing, revocation and teardown.

## Code backlog

### P0 — required for the chosen event architecture

#### A. Event model proxy

1. Add a separately deployed, OpenAI-compatible proxy service under `deploy/`.
   Keep it outside experiment execution and outside the generic `efferents/`
   domain model.
2. Route a dedicated HTTPS path or hostname to it. Do not reuse the organizer
   Basic Auth credential as a model token.
3. Add token creation, hashed storage, expiration, revocation, per-token limits
   and a total event limit. Logs should contain token identifiers and usage,
   not token values or prompt bodies by default.
4. Verify whether the existing `EFFERENTS_API_BASE` plus `OPENAI_API_KEY`
   behavior is sufficient for a proxy token. If it is, document it and avoid a
   new client abstraction. If it is not, add explicit generic proxy settings
   in `efferents.agents.model_client` and include the proxy token in the
   daemon-only credential filter.
5. Add failure classification and clear participant diagnostics for revoked,
   expired and quota-exhausted event tokens.

#### B. Remote event registry and heartbeat

1. Define a versioned, event-neutral protocol. Minimum shared snapshot:
   `event_id`, `lab_id`, `domain`, optional `topic` and `approach`, runtime
   status, last activity time, headline metric name/direction/latest value,
   run count, verdict status and a coarse local budget state. Exclude source,
   config bodies, prompts, credentials, raw data, full artifacts and private
   steering.
2. Add an event join flow that exchanges an enrollment code for a lab-scoped,
   revocable token and writes local credentials with mode 600.
3. Add `efferents event join`, `efferents event sync`, `efferents event status`
   and `efferents event leave` commands. `leave` stops future sharing without
   rewriting previous server records.
4. Add a bounded heartbeat from the local daemon at safe step boundaries and
   a manual sync path. Queue the latest snapshot locally while offline and
   retry with backoff.
5. Store server state durably with append-only registration/status history and
   a current-view index. Mark a lab stale rather than deleting it when its
   heartbeat stops.
6. Authenticate writes per lab, validate identifiers and payload sizes, reject
   unknown fields, rate-limit clients and make retries idempotent.

#### C. Read-only live network

1. Feed the existing network UI from remote event snapshots instead of the
   server's local `~/.efferents/registry.json` alone.
2. Visually distinguish `running`, `paused`, `stopped`, `stale` and offline
   fallback/sample nodes.
3. Make remote lab detail read-only. Never map a remote node to the current
   local organizer control context or expose start/stop/steer endpoints.
4. Remove or clearly label illustrative talks and experiments when the page is
   in live mode.
5. Add an organizer display route suitable for projection and an export of the
   final event snapshot.

#### D. Event intake and packaging

1. Replace `intake.md`'s current event-hosted mode with the local-execution
   flow. Preserve its explicit launch-contract approval before repository
   commands run.
2. Add a versioned starter repository/template and a one-command install path.
3. Add the one-page quickstart and ChatGPT web handoff prompt.
4. Ensure every generated `.gitignore` covers local Efferents credentials,
   event tokens, `.env*`, lab runtime state and cached artifacts as intended.
5. Add a local `event doctor` or equivalent preflight that checks tools, proxy
   reachability, token validity, writable paths and starter smoke execution
   without spending a full agent turn.

#### E. Starter lab

Implement the congestion-aware evacuation lab described in the next section,
including meaningful tests for determinism, metric correctness, config
validation and the falsifier—not tests that merely mirror implementation.

### P1 — valuable if time remains after P0 rehearsal

1. Refactor `efferents.agents.conference` behind a transport interface so its
   current same-host filesystem behavior and a new authenticated HTTP feed can
   share the same provenance and response rules.
2. Sync content-hashed hypothesis/talk/finding snapshots through the server.
   Keep excerpts bounded and sharing opt-in.
3. Support question/reply round trips and render them as live network edges.
4. Add an explicit reproduction request/bundle path; receipt never implies
   corroboration, and a challenge requires a local reproduction run.
5. Add per-participant proxy usage visibility without revealing other
   participants' details.
6. Add an event summary export from consented snapshots.

### P2 — after the event or only if P0 is comfortably green

1. Participant accounts and role-separated web access.
2. Private hosted group sync beyond event summaries.
3. Multiple runnable starter domains and a starter chooser.
4. Cross-platform installers, especially a rehearsed Windows path.
5. Rich central evidence browsing. This must remain explicit publication or
   sharing, not automatic upload of local evidence.
6. Reusable event creation, archival and retention controls.

## Recommended starter lab: congestion-aware evacuation

### Why this one

It is visual, understandable without domain expertise, fast on a CPU and
genuinely open to competing approaches. It produces paired quantitative
evidence rather than a decorative simulation. Multiple participants can work
on the same topic using different approaches, which makes the network useful
and creates natural reproduction and challenge exercises.

### Research question

> Across the same procedurally generated building layouts, can a routing policy
> that reacts to congestion evacuate agents faster than static shortest-path
> routing without reducing the completion rate?

### Seed hypothesis

A local congestion-aware routing policy reduces median evacuation time by at
least 10% relative to static shortest-path routing across at least 12 paired
scenario seeds, while both policies evacuate at least 95% of agents before the
time limit.

The implementation session must run this through Popper Probe and preserve the
approved result; the wording here is a candidate, not a pre-approved
`hypothesis.md`.

### Experiment shape

- Generate small grid buildings from a seed: rooms, corridors, exits and a
  fixed number of evacuees.
- Run the exact same layout and starting positions under the baseline and
  candidate policy.
- Baseline: static shortest path to the nearest exit.
- Initial candidate: reroute periodically using local edge occupancy as an
  added path cost.
- Keep the simulator deterministic for a `(config, seed)` pair.
- Use Python standard library for the core simulator. Emit an SVG or compact
  HTML replay so no plotting dependency is required.
- One Efferents run may evaluate one paired seed and emit both arm results plus
  their delta. A bounded starter batch should cover enough seeds for the
  falsifier to become decidable.

### Metrics and validity

- Headline: `evacuation_improvement_pct`, maximize.
- Validity constraint: `candidate_completion_rate >= 0.95`.
- Supporting metrics: baseline and candidate median steps, p95 steps,
  completion rates, congestion waits, reroute count and wall time.
- Provenance fields: seed, layout hash, policy name and policy parameters.
- Falsifier: after at least 12 valid paired seeds, median improvement is less
  than 10%, or candidate completion falls below 95%.
- Artifact: one representative side-by-side route/congestion replay plus a
  machine-readable summary.

### Participant research directions

Each card should offer a direction, not a recipe:

1. **Local congestion cost:** choose how occupancy changes route cost.
2. **Adaptive rerouting:** decide when an agent should abandon its current
   route.
3. **Exit balancing:** assign or price exits to avoid crowd collapse.
4. **Staggered release:** test whether small departure delays reduce total
   clearance time.
5. **Randomized diversity:** add controlled route variation to prevent herding.
6. **Capacity-aware search:** include corridor throughput in path planning.
7. **Robustness:** optimize for blocked exits or sensor noise rather than the
   nominal layouts.

These support distinct `approach` values and can become separate labs without
pretending identical copies are independent research groups.

### Proposed starter repository layout

```text
starter-evacuation-lab/
  README.md
  lab.yaml
  hypothesis.md
  context/popper.md
  configs/default.yaml
  popper-corpus/<slug>/hypothesis.md
  src/
    simulate.py
    policies.py
    render.py
    run_experiment.py
  tests/
    test_simulation.py
    test_metrics.py
```

The default Coder mode should be `review`. Config-only changes give beginners a
safe path; advanced participants can review and apply a proposed policy diff.

### Starter-lab acceptance checks

- A clean install and `efferents validate` succeed from documented commands.
- Identical config and seed produce byte-stable metrics and equivalent
  artifacts.
- Baseline and candidate see identical layouts and starting positions.
- A deliberately broken policy fails the completion constraint.
- The falsifier stays `insufficient_data` below its sample threshold and becomes
  decided at the documented threshold.
- One run finishes in under 30 seconds on the event's slowest test laptop.
- A bounded autonomous cycle finishes within the measured event time and local
  and server-side spend caps.
- The local dashboard shows the hypothesis, eligible/excluded runs, verdict,
  provenance and artifact without domain-specific UI code.
- Only the allowed event summary appears on the server.

## Other viable starter ideas

These are backups, not additional P0 commitments.

| Idea | Question | Strength | Main risk |
| --- | --- | --- | --- |
| Robust delivery routing | Which heuristic preserves on-time deliveries when roads fail? | Visual, fast, many approaches | Similar to many optimization demos |
| Ecosystem intervention | Which bounded intervention stabilizes a predator-prey simulation? | Engaging dynamics and plots | Easy to overclaim real ecological relevance |
| Fair conference scheduling | Can a schedule reduce participant conflicts without concentrating inconvenience? | Event-relevant and understandable | Needs a credible synthetic preference generator |
| Prompt compression | How much prompt context can be removed without lowering task accuracy? | Uses the event model directly | Judge design, cost and provider variance complicate the first event |
| Error-correcting message relay | Which redundancy scheme survives a fixed noisy channel at lowest cost? | Clear metrics and fast execution | Less immediately visual |

The evacuation lab is the best first choice because it exercises config search,
code proposals, paired evidence, visual artifacts and distinct lab approaches
without external data, GPU access or LLM-as-judge metrics.

## Suggested implementation order

1. Build and verify the evacuation simulator as a fully offline experiment.
2. Wrap it in the Efferents lab contract and pass the hypothesis gate.
3. Stand up the model proxy and prove one bounded local autonomous cycle.
4. Implement remote join and heartbeat with a read-only projected network.
5. Rewrite intake and quickstart around the working commands.
6. Run the two-laptop rehearsal and create the offline result bundle.
7. Add remote talks/replies only if the minimum path is already stable.

## Date decision gate

Keep Tuesday 2026-09-22 only if the P0 path passes a two-laptop rehearsal before
the conference begins or, at the latest, before leaving for it. If the proxy,
starter lab or remote heartbeat still needs first-time integration after Sunday
2026-09-20, move the event to Thursday 2026-09-24. Monday should be reserved for
rehearsal and fixes, not for discovering the architecture.

The LinkedIn post remains the only known unfinished general event-planning item.
Publish it immediately after the date is fixed; it should promise only the
minimum experience that has passed rehearsal.
