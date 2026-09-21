# Autoresearch Night operator runbook

This is the minimum live-event contract. The [participant quickstart](event-quickstart.md)
and [DigitalOcean deployment guide](digitalocean.md) contain the commands.
Participant labs execute locally. The hosted server brokers a bounded model
API, displays status snapshots, and exchanges bounded findings for labs that
explicitly join with `--share-findings`. The console also supports local idea
onboarding on the machine hosting it; do not use that organizer control to
upload or run participants' private repositories.
The owner-provided credit and provider decision is recorded in
[event Azure context](../context/event_azure_decision.md).

## Decisions frozen for the first event

- Scope: one private organizer console and one event. Labs may name a common
  `research_goal` while pursuing distinct approaches, or work independently
  across domains. The graph groups by goal/domain and shows real finding
  receipts. A receipt means imported into a research inbox, not corroboration.
  Remote participants opt in separately to measurements and agent discussion;
  raw evidence remains on their laptops.
- Lanes: local browser idea onboarding or coding-agent setup; ChatGPT web can
  generate files for local execution. Infer-and-run accepts the displayed
  bounded scope in one step. New starters use lightweight contracts with no
  Popper dependency; deeper Popper review remains optional. macOS is
  locally tested; Linux is conditional on a real-laptop rehearsal; Windows is
  out of scope for this first event.
- Model: three Azure OpenAI v1 Chat Completions deployments, mapped to event
  `fast`, `standard`, and `deep` aliases. The selected models are GPT-4.1
  nano, GPT-5.6 Luna, and GPT-5.6 Sol respectively. Verify these exact models and
  versions are available in the owner's subscription. Prices are explicit operator
  configuration, never inferred from the $5,000 credit balance. Changing a
  deployment or price requires another cost and live-tool-call rehearsal.
  GPT-5.6 function-tool calls use `reasoning_effort=none` on Chat Completions;
  no-tool calls retain medium reasoning. Full reasoning with tools requires a
  future Responses API integration and is not claimed for this event.
- Limits: $3 per event token, $50 event total, 30 requests/minute/token, 16,384
  output tokens/request; the starter additionally caps a lab at $1/day and
  $2 total. One token belongs to one generated unique lab ID. The organizer
  must monitor Azure billing separately; Azure budget alerts do not stop spend.
- Heartbeat: at daemon start and safe step boundaries, with manual sync and
  offline queue. Mark a running lab stale after 180 seconds without a received
  heartbeat. Paused and stopped states remain explicit.
- Event close: participants stop locally and send a stopped heartbeat;
  organizer exports the consented summary, runs `admin close`, then
  `admin purge-tokens`. Closure cannot be undone by a container restart.
- Retention: announce the summary retention date to attendees before join;
  retain no token hashes or detailed model-request rows after close. The
  organizer deletes the remaining event database and exports at that date.

The earlier planning document listed 22 and 24 September 2026 as candidate
dates. Do not advertise either as confirmed solely from repository state.
Use 24 September as the *operational target* only if the organizer confirms it
with attendees. The 22 September option is go only after a real two-laptop
rehearsal by 20 September; otherwise move it. No code-only test can replace
the venue/network rehearsal.

## Before any attendee receives the URL

1. Publish a reviewed, immutable release from the organization repository
   (`Entangled-Research/efferents`). Until then, the quickstart's Git clone
   fetches old code. Do not use an uncommitted checkout as the advertised
   participant install source. Public release/push needs explicit owner approval.
2. Prepare the server per the deployment guide. Generate distinct 32-byte
   enrollment/admin secrets, set `.env` mode 600, verify hostname/TLS and
   organizer Basic Auth, then validate Caddy and Compose. The event service
   refuses weak secrets, insecure public URLs, invalid limits, and blank vendor
   keys at startup.
3. Use the Azure resource's v1 endpoint and key. Confirm the three deployments
   are **sold directly by Azure**, charged to the credited subscription, and
   support Chat Completions with tool calls. Record actual input/output prices
   for each deployment type and region in the private `.env`; check Azure Cost
   Management and credit balance after a test request. Keep the event's $50
   total cap until the real rehearsal passes. See the Azure connection steps
   in [the deployment guide](digitalocean.md#connect-microsoft-azure).
4. Build the no-model result bundle while online:

   ```bash
   uv run python scripts/build_event_fallback.py --out event-output/fallback
   ```

   Copy `event-output/fallback.zip` to the organizer laptop and USB drive.
   Install/cache the framework's third-party dependencies on that laptop
   beforehand; the included wheel alone is not an offline dependency mirror.
   Turn Wi-Fi off and open the saved `progress.html`, ledger, and SVG, then
   rerun a local configuration change.
5. Back up the event database before rehearsal. From the Droplet's
   `/opt/efferents/deploy/digitalocean`, run:

   ```bash
   docker compose exec event-service python /app/app.py admin backup
   # Copy the printed /data/event/backup-...sqlite path out of the container:
   docker cp "$(docker compose ps -q event-service)":/data/event/backup-REPLACE.sqlite \
     /root/event-backup.sqlite
   chmod 600 /root/event-backup.sqlite
   ```

   The command uses SQLite's online backup API, so it includes committed WAL
   data. Keep the backup private: it contains token hashes and usage history.
   Restore it to a separate test instance and verify the network projection
   and accounting before claiming recovery works. Delete old backups at the
   announced retention date, including copies outside the Docker volume.

## Rehearsal and go/no-go

Use the actual hostname, Azure resource key, venue Wi-Fi and projector,
one organizer machine and two separate participant laptops. Start each from a
clean temporary directory and the published release. Record installation
time, first model response, first successful experiment, network update delay,
and server/model spend.

1. On each laptop, create the starter, confirm distinct `lab_id` values, review
   the launch contract, validate the Popper corpus, then join at the hidden
   enrollment prompt. `event doctor` must pass, including the no-cost model
   route and offline smoke command. Exercise fast, standard, and deep calls,
   including a real tool call, and verify the Azure deployment meters.
2. Run one bounded live cycle. Verify a local `runs.sqlite`, notebook,
   dashboard, artifact, explicit falsifier state, and owner budget. Run manual
   sync and verify both remote nodes show only allowed fields and are not
   clickable as local controls.
   With finding exchange enabled, use the same goal on two labs and a different
   domain on a third. Verify goal-related receipt delivery and a cross-domain
   receipt on the third visit. Compare the graph receipt with each recipient's
   durable `lab/conference/inbox.jsonl`; no arrow may imply reproduction.
3. Disconnect laptop A for more than 180 seconds. Its node must become stale,
   while laptop B continues. Reconnect A and sync; its node must recover.
4. Revoke A's token by token ID. Its next model request and heartbeat must fail
   clearly; B must remain functional. A's local evidence must still open.
5. Stop both labs and inspect final status. Confirm the fallback works without
   Wi-Fi or provider access. Check Caddy/auth behavior from outside the server:
   organizer `/api/control` rejects anonymous requests and bearer `/v1/models`
   rejects missing tokens.

No-go if any of these fail: remote/public auth boundary, provider terms or
hard budget backstop, live model response, two independent nodes, participant
stop/steer, local evidence retention, or offline fallback. A green unit suite
is necessary but not sufficient. Freeze features after a passing rehearsal.

## During the event

- Show `#network` on the projector. Explain dotted goal/domain relationships
  and solid directed finding receipts. The feed contains opted-in summaries;
  accepted scientific evidence still requires local reproduction.
- Watch `docker compose ps`, `docker compose logs --tail=80 caddy gateway
  event-service`, and `admin tokens`; never display `.env`, raw token values,
  participant prompts, or the organizer admin key.
- For 401/403, run participant `event status` and `event doctor`; distinguish
  invalid, revoked, left, and expired tokens. For 402, inspect token/total
  spend. For 429, allow backoff. For 503, inspect provider/service health and
  switch to the offline fallback if it persists.
- If a token leaks, revoke only its token ID. If the enrollment code leaks,
  rotate it in `.env` and rebuild/restart `event-service`; existing tokens
  remain usable. If the vendor key leaks, revoke it at the provider, update
  `.env`, and restart the event service.
- Never put a participant repository or evidence bundle on the hosted server
  to solve a setup problem. Help them use the local fallback instead.

## Five-minute close

Announce five minutes remaining. Ask everyone to stop their local daemon,
run `efferents status --submission .`, locate `lab/runs.sqlite`,
`lab/lab_notebook.md`, and `lab/artifacts/`, then run `efferents event sync`.
Export, close, and purge using the deployment guide. Verify a new join gets
HTTP 410 and no existing token can make a model call. Save the consented export
with mode 600, then stop the server when no longer needed. Send follow-up links
only after their final URLs and feedback/consent wording are approved.
