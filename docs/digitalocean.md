# Host the first Efferents workspace on DigitalOcean

This deploys the private event control plane: an HTTPS organizer console, a
read-only projected network of sanitized participant heartbeats, and a separate
OpenAI-compatible model proxy. Participant labs execute on their own laptops;
their source, raw data, prompts, evidence, artifacts, steering, and local budget
ledger are not uploaded.

**Scope:** one trusted organizer and revocable per-lab event tokens. Keep the
organizer login and event admin key to yourself. Participants receive the
enrollment code at the event and exchange it for individual opaque tokens.
Bounded measurements and agent discussion can be shared with explicit
`--share-findings` consent. The graph displays receipt records, not automatic
corroborations. Reproduction requests and participant web accounts remain
outside this deployment. Browser idea creation runs on the console's host;
participants use their own local console or the participant CLI.

## 1. Create a small server

In the [DigitalOcean dashboard](https://cloud.digitalocean.com/), choose
**Create → Droplets**:

- Region: London (or close to your event).
- Image: Ubuntu **24.04 LTS**, x64.
- Plan: **Basic → Regular → 1 vCPU / 2 GB RAM**. If the UI offers a choice
  between bundled plans and v5 configurations, choose **Bundled** for this plan.
- Authentication: your SSH public key.
- Hostname: `efferents-events`.

The published price for this bundled plan was **$12/month** on 11 September
2026; check the creation screen before confirming. This is a starting size for
the console and small CPU experiments, not a capacity estimate for an entire
event. Model tokens, GPUs, backups, and other add-ons are separate.
[DigitalOcean pricing](https://www.digitalocean.com/pricing/droplets).

If you need an SSH key, run `ssh-keygen -t ed25519 -C efferents-events` in your
Mac's Terminal. Use an existing key if prompted about overwriting one. Copy the
contents of its `.pub` file into DigitalOcean's **New SSH Key** field; your
private key stays on your laptop.

After creation, copy the Droplet's public IPv4 address. Under **Networking →
Firewalls**, create and attach a Cloud Firewall allowing inbound TCP **22**
from your IP, and TCP **80** and **443** from all IPv4/IPv6 addresses. Keep the
default outbound rules. Do not open port 8800: Efferents binds to loopback and
Caddy provides the authenticated public entry point.

## 2. Upload this checkout

These commands run in your **Mac's Terminal**, from the Efferents checkout.
Replace the example IP with the real one. This includes the deployment changes
in your working tree; they do not need to be pushed to GitHub first.

```bash
cd /Users/masha/Documents/efferents
EVENT_IP=203.0.113.10
tar --exclude=__pycache__ --exclude='*.pyc' --exclude=.env \
  --exclude=.env.live --exclude=.git --exclude=lab \
  -czf /tmp/efferents-hosting.tgz \
  pyproject.toml README.md LICENSE NOTICE .dockerignore \
  efferents deploy/digitalocean deploy/event_gateway \
  docs/prototypes/event-network.html examples/smoke-lab
ssh root@"$EVENT_IP" 'mkdir -p /opt/efferents'
scp /tmp/efferents-hosting.tgz root@"$EVENT_IP":/tmp/
ssh root@"$EVENT_IP"
```

Check the SSH host fingerprint against the Droplet console when connecting for
the first time. All remaining commands run **on the Droplet**, unless stated
otherwise.

```bash
tar -xzf /tmp/efferents-hosting.tgz -C /opt/efferents
```

The automatic deployment described below checks out the canonical GitHub
repository on a GitHub-hosted runner and syncs only the deployment inputs; the
Droplet does not need its own Git checkout. The upload above also works while
the files exist only locally.

## 3. Install Docker

On a fresh Ubuntu Droplet, install from Docker's official apt repository:

```bash
apt-get update
apt-get install -y ca-certificates curl
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
cat > /etc/apt/sources.list.d/docker.sources <<'EOF'
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Signed-By: /etc/apt/keyrings/docker.asc
EOF
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
systemctl enable --now docker
docker compose version
```

These commands target **Ubuntu 24.04**. For another OS or an existing Docker
installation, use [Docker's installation instructions](https://docs.docker.com/engine/install/ubuntu/).
The deployment uses **Linux host networking**, so it is intended for the
Droplet, not Docker Desktop's default networking on a Mac.

## 4. Set the URL, event limits, and secrets

Generate a password hash interactively; the password will not be put in shell
history:

```bash
docker run --rm -it caddy:2-alpine caddy hash-password
cd /opt/efferents/deploy/digitalocean
cp .env.example .env
chmod 600 .env
nano .env
```

Set the organizer login, event identity/expiry, generated enrollment/admin
secrets, server-side caps, and Azure resource key. Substitute your actual IP, using
dashes, and paste the complete Basic Auth hash **inside single quotes**:

```dotenv
EFFERENTS_HOSTNAME=203-0-113-10.sslip.io
EFFERENTS_AUTH_USER=organizer
EFFERENTS_AUTH_HASH='$2a$...paste the complete generated hash here...'
EVENT_ID=autoresearch-night-2026-09
EVENT_ENROLLMENT_CODE=...random value shown only to participants...
EVENT_ADMIN_KEY=...different random value kept by organizer...
EVENT_EXPIRES_AT=2026-09-25T00:00:00+00:00
EVENT_TOKEN_CAP_USD=3.0
EVENT_TOTAL_CAP_USD=50.0
EVENT_AZURE_OPENAI_BASE=https://YOUR-RESOURCE.openai.azure.com/openai/v1
EVENT_AZURE_OPENAI_API_KEY=...Azure resource key...
EVENT_AZURE_FAST_DEPLOYMENT=...name of your fast deployment...
EVENT_AZURE_FAST_INPUT_USD_PER_MTOK=...actual price...
EVENT_AZURE_FAST_OUTPUT_USD_PER_MTOK=...actual price...
EVENT_AZURE_STANDARD_DEPLOYMENT=...name of your standard deployment...
EVENT_AZURE_STANDARD_INPUT_USD_PER_MTOK=...actual price...
EVENT_AZURE_STANDARD_OUTPUT_USD_PER_MTOK=...actual price...
EVENT_AZURE_DEEP_DEPLOYMENT=...name of your deep deployment...
EVENT_AZURE_DEEP_INPUT_USD_PER_MTOK=...actual price...
EVENT_AZURE_DEEP_OUTPUT_USD_PER_MTOK=...actual price...
```

The example address will not work; replace it. Do not include `https://` or a
path in `EFFERENTS_HOSTNAME`. The single quotes preserve dollar signs in the
hash when Compose reads `.env`. Generate the two event secrets independently
with `python -c 'import secrets; print(secrets.token_urlsafe(32))'`. The vendor
key is passed only to `event-service`; it is not present in the organizer
gateway or participant labs. The three aliases are configured to Azure
deployment names server-side. Prices are set from your actual Azure rates and
are shared with local labs for budget estimates. The server independently
enforces the event token and total caps.

### Connect Microsoft Azure

1. Sign in to the Azure portal with the account that received the $5,000
   credits. Confirm the correct **subscription**, credit balance, expiration,
   and whether Azure OpenAI consumption is eligible for that offer. Microsoft
   for Startups guidance covers models sold directly by Azure, not
   partner/Marketplace-billed models. Check your own offer terms.
2. In Microsoft Foundry, create or select an Azure OpenAI resource in a region
   with capacity. Deploy three **pay-as-you-go** Chat Completions-compatible
   models: GPT-4.1 nano (`fast`), GPT-5.6 Luna (`standard`), and
   GPT-5.6 Sol (`deep`). Record the exact deployment names, not merely model IDs.
   Check the GPT-5.6 quota for your subscription; access is not automatic.
   Avoid Provisioned Throughput for this short event unless explicitly costed.
3. From the resource's endpoint/key page, copy the resource HTTPS endpoint
   and append `/openai/v1` to form `EVENT_AZURE_OPENAI_BASE`. Put the key only
   in the private `deploy/digitalocean/.env` as `EVENT_AZURE_OPENAI_API_KEY`.
   Never paste it into a participant lab or a support message.
4. Fill all three deployment names and their **actual** per-million input and
   output token prices in `.env`; verify the region, deployment type, and
   billing meter. Keep `EVENT_TOKEN_CAP_USD=3.0` and
   `EVENT_TOTAL_CAP_USD=50.0` for rehearsal. Azure cost budgets provide alerts,
   not automatic shutdown.
5. Start the services below. Join a throwaway local lab, run `efferents event
   doctor`, then make one fast, standard, and deep request through its event
   token, including a tool call. Confirm each hit its intended deployment and
   appears in Azure usage/Cost Management. Revoke that token afterward. Do a
   two-laptop venue rehearsal before distributing the live enrollment code.

Azure references: [v1 endpoint and key](https://learn.microsoft.com/en-us/azure/foundry/openai/api-version-lifecycle),
[model deployment](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/how-to/deploy-foundry-models),
[startup-credit coverage](https://learn.microsoft.com/en-us/startups/benefits/technical-benefits/azure-credits/foundry-model-sponsorship-coverage),
[budget alerts](https://learn.microsoft.com/en-us/azure/cost-management-billing/costs/cost-mgt-alerts-monitor-usage-spending).

[sslip.io](https://nip.io/) provides DNS for an IP embedded in a hostname, so no
domain registration is necessary. Caddy obtains and renews the HTTPS certificate
when ports 80 and 443 are reachable. For a domain you own, create an A record
such as `events.example.com` pointing to the Droplet and use that hostname
instead. Do not create an AAAA record unless that IPv6 address reaches this
server. [Caddy HTTPS documentation](https://caddyserver.com/docs/automatic-https).

```bash
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs --tail=50 caddy gateway event-service
docker compose exec caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
```

Open **`https://YOUR-DASHED-IP.sslip.io`** and log in as `organizer` with the
password you chose. Open `#network` for the projector. The first image build and
certificate request can take a few minutes; the event registry starts empty.

## 5. Verify the hosted event boundary

The event service should be loopback-only and healthy. Its database lives in a
separate named volume:

```bash
curl http://127.0.0.1:8801/healthz
docker compose exec event-service python /app/app.py admin tokens
docker compose ps
```

Check public authentication from your laptop (substitute the real hostname):

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://YOUR-HOSTNAME/api/control
curl --user organizer https://YOUR-HOSTNAME/api/labs
curl -s -o /dev/null -w '%{http_code}\n' https://YOUR-HOSTNAME/v1/models
```

The first request must return **401**. The second prompts for the organizer
password and should return `event_network.configured: true`. The third must
also return **401**, but from the bearer-token event API rather than a Basic
Auth browser challenge. Never expose organizer routes if the first request
returns 200. Use HTTPS for every public request.

## 6. Rehearse two participant laptops

Follow the [participant quickstart](event-quickstart.md) on two separate
machines. Each lab joins with the same enrollment code but receives a different
token. Run a bounded local cycle and `efferents event sync`; both labs should
appear under `#network` without source or evidence details. Disconnect one
laptop for longer than `EVENT_STALE_AFTER_SECONDS` and confirm only its node
becomes `stale`.

List token ids and aggregate spend without exposing token values:

```bash
docker compose exec event-service python /app/app.py admin tokens
```

Revoke one token and verify only that participant receives the clear revoked
diagnostic while the other still syncs and uses the proxy:

```bash
docker compose exec event-service python /app/app.py admin revoke TOKEN_ID
```

Participant-side local `budget.daily_cap_usd` and `budget.total_cap_usd` remain
the second spend boundary. The event service enforces its per-token cap and
total ceiling before forwarding a request, logs token ids and usage only, and
does not persist prompt bodies.

## Persistence, updates, and scaling

### Deploy `main` automatically after CI

The `deploy-digitalocean` job in `.github/workflows/ci.yml` runs only for a
push to `main`, after both CI test jobs pass. It syncs the tracked deployment
inputs, rebuilds the Compose services, and checks the two loopback health
endpoints. It never copies the deployment `.env`, and `docker compose up`
preserves the named volumes.

Set up a dedicated deployment key and user once. On the Mac, generate a key
that is used only by GitHub Actions:

```bash
ssh-keygen -t ed25519 -f "$HOME/.ssh/efferents_github_deploy" -C github-actions-efferents
```

On the Droplet, create the deployment user and give it access to Docker and the
existing application directory:

```bash
adduser --disabled-password --gecos '' efferents-deploy
usermod -aG docker efferents-deploy
apt-get update && apt-get install -y rsync
install -d -m 0700 -o efferents-deploy -g efferents-deploy /home/efferents-deploy/.ssh
chown -R efferents-deploy:efferents-deploy /opt/efferents
```

From the Mac, install only the new public key for that user:

```bash
ssh-copy-id -i "$HOME/.ssh/efferents_github_deploy.pub" efferents-deploy@YOUR_DROPLET_IP
ssh -i "$HOME/.ssh/efferents_github_deploy" efferents-deploy@YOUR_DROPLET_IP \
  'docker compose version && test -f /opt/efferents/deploy/digitalocean/.env'
```

Record the Droplet host key only after comparing its fingerprint with the host
key shown from a trusted Droplet console session:

```bash
ssh-keyscan -t ed25519 YOUR_DROPLET_IP > /tmp/efferents-known-hosts
ssh-keygen -lf /tmp/efferents-known-hosts
# On the Droplet console, compare with:
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

In the GitHub repository, create an environment named `production` under
**Settings → Environments**. Do not add a required reviewer if every successful
`main` push should deploy without a manual approval. Add these environment
secrets:

| Secret | Value |
| --- | --- |
| `DEPLOY_HOST` | Droplet IP address or hostname |
| `DEPLOY_USER` | `efferents-deploy` |
| `DEPLOY_SSH_KEY` | Entire contents of `~/.ssh/efferents_github_deploy` |
| `DEPLOY_KNOWN_HOSTS` | Entire contents of `/tmp/efferents-known-hosts` |

After the key, firewall, and all four secrets are ready, open **Settings →
Secrets and variables → Actions → Variables** and create the repository
variable `DIGITALOCEAN_DEPLOY_ENABLED` with the value `true`. Until this switch
is enabled, pushes still run CI but deliberately skip the deployment job.

GitHub-hosted runners must be able to reach TCP port 22 on the Droplet. A
DigitalOcean firewall restricted only to the operator's home IP will block the
workflow. The simplest configuration permits port 22 from the internet while
OpenSSH allows key authentication only; keep password authentication disabled
and use the dedicated key above. If that exposure is unacceptable, use a
self-hosted runner or private network tunnel instead of widening the firewall.
GitHub's hosted-runner address ranges are broad and change over time, so a
copied static allowlist is not reliable.

After enabling the switch, open **GitHub → Actions → ci → Run workflow** and
select `main` for the first deployment. Both `test` matrix jobs must finish
before `deploy DigitalOcean` starts. Later pushes to `main` deploy
automatically. The `production` environment deployment history records the
exact commit deployed.

The deploy job serializes updates rather than cancelling a running deployment.
If a deploy fails, inspect that job's logs and the server-side Compose logs; do
not rerun with volume deletion.

### Manually refresh the hosted web

The server-side `.env` and all named volumes are deliberately excluded from the
upload. From the local checkout:

```bash
cd /Users/masha/Documents/efferents
EVENT_IP=YOUR_DROPLET_IP
tar --exclude=__pycache__ --exclude='*.pyc' --exclude=.env \
  --exclude=.env.live --exclude=.git --exclude=lab \
  -czf /tmp/efferents-hosting.tgz \
  pyproject.toml README.md LICENSE NOTICE .dockerignore \
  efferents deploy/digitalocean deploy/event_gateway docs/prototypes/event-network.html
scp /tmp/efferents-hosting.tgz root@"$EVENT_IP":/tmp/
ssh root@"$EVENT_IP"
```

On the Droplet, add any newly required keys from `.env.example` to the existing
mode-600 `.env`, then rebuild in place:

```bash
tar -xzf /tmp/efferents-hosting.tgz -C /opt/efferents
cd /opt/efferents/deploy/digitalocean
docker compose config --quiet
docker compose up -d --build --remove-orphans
docker compose ps
docker compose logs --tail=80 caddy gateway event-service
docker compose exec caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
curl http://127.0.0.1:8800/api/labs
curl http://127.0.0.1:8801/healthz
```

Do not run `docker compose down -v`. Reload
`https://YOUR-HOSTNAME/#network`; use a hard refresh if the already-open tab has
old CSS or JavaScript. The API responses carry `Cache-Control: no-store`, so a
normal reload is normally sufficient.

- `event_data` stores the event token hashes, quotas, request usage, and
  append-only/current heartbeat records. `efferents_data` remains available for
  organizer-local console state. Caddy certificates have separate persistent
  volumes. Keep the same Compose project name when updating.
- The console, event service, and Caddy restart after a server reboot.
- Participant labs do not run in these containers, so a hosted refresh cannot
  interrupt their local execution. To update, upload the revised files and run
  `docker compose up -d --build`. To stop the control plane, use
  `docker compose stop`. Do not add `--volumes` to `down`: it deletes the event
  registry, quota history, and certificate storage.
- Take a Droplet snapshot/backup after stopping labs and before resizing or
  replacing the server. A persistent volume survives container replacement,
  but does not substitute for a backup of the Droplet.
- Before the event, test representative workloads and inspect CPU, memory,
  storage, and model spend. Resize based on that measurement. To scale down
  afterward, use **CPU and RAM only / keep storage fixed**. Disk growth cannot
  be undone. Resizing requires shutdown and some downtime, so do it before
  participants arrive. [DigitalOcean resizing guide](https://docs.digitalocean.com/products/droplets/how-to/resize/).
- Turning off a Droplet does not stop its reservation charges. After the event,
  retain it at an appropriate size or back up and destroy it when no longer
  needed. Check separately billed backups/snapshots too.
  [DigitalOcean billing](https://docs.digitalocean.com/products/droplets/details/pricing/).

### Event close and retention

After participants stop locally and send their final stopped heartbeat, export
the consented network summary, revoke every remaining credential, then delete
credential hashes and detailed token accounting rows:

```bash
docker compose exec event-service python /app/app.py admin export > "$PWD/event-summary.json"
docker compose exec event-service python /app/app.py admin close
docker compose exec event-service python /app/app.py admin purge-tokens
chmod 600 "$PWD/event-summary.json"
```

`close` durably disables enrollment and revokes active tokens; a container
restart cannot reopen that event. `purge-tokens` requires closure and deletes
credential hashes, per-token usage, and append-only heartbeat history, while
leaving the consented current network summary and aggregate spend. Keep the
exported summary and remaining event database only for the announced retention
period. Participant repositories and evidence never depended on the event
credential.

## If the link does not open

Run `docker compose ps` and
`docker compose logs --tail=100 caddy gateway event-service` from
`/opt/efferents/deploy/digitalocean`. All three containers must be healthy. On
the Droplet, both `curl http://127.0.0.1:8800/api/labs` and
`curl http://127.0.0.1:8801/healthz` should return JSON.

If local JSON works but HTTPS does not, check the hostname's DNS, the attached
firewall's ports 80/443, and Caddy's certificate logs. If sslip.io certificate
issuance is rate-limited, use a hostname under your own domain or try the
equivalent nip.io hostname and recreate Caddy. Keep authentication enabled
while resolving certificate or network issues.

If a participant reports missing credentials, have them run
`efferents event status --submission .` and `efferents event doctor
--submission .`. Distinct diagnostics identify invalid, expired, revoked,
quota-exhausted, rate-limited, and temporarily unavailable proxy states. Their
local evidence remains available throughout an event outage.
