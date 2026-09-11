# Host the first Efferents workspace on DigitalOcean

This deploys the existing research console at an HTTPS URL with an organizer
login. You can connect a lab, run it on the server, inspect evidence, and steer
or stop it. A domain purchase and model API key are not needed for the first
smoke test.

**Scope:** one trusted organizer. Keep the login to yourself. The current
workspace has a shared selected lab and no per-student permissions or execution
isolation. Participants' laptop labs do not automatically register with this
server. Network lines indicate shared domains; automatic exchange of findings,
replication, and challenges across machines is a subsequent implementation step.
Manual journal and evidence-bundle exchange already exists in
`efferents.agents.federation`.

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
  --exclude=.env.live --exclude=.git --exclude=lab --exclude=popper-corpus \
  -czf /tmp/efferents-hosting.tgz \
  pyproject.toml README.md LICENSE NOTICE .dockerignore \
  efferents deploy/digitalocean examples/smoke-lab
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

For future deployments from GitHub, verify `origin` points to
`https://github.com/Entangled-Research/efferents.git` before pulling. The upload
above works even while these files exist only locally.

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

## 4. Set the URL and organizer password

Generate a password hash interactively; the password will not be put in shell
history:

```bash
docker run --rm -it caddy:2-alpine caddy hash-password
cd /opt/efferents/deploy/digitalocean
cp .env.example .env
chmod 600 .env
nano .env
```

Set these three values. Substitute your actual IP, using dashes, and paste the
complete hash from the preceding command **inside single quotes**:

```dotenv
EFFERENTS_HOSTNAME=203-0-113-10.sslip.io
EFFERENTS_AUTH_USER=organizer
EFFERENTS_AUTH_HASH='$2a$...paste the complete generated hash here...'
```

The example address will not work; replace it. Do not include `https://` or a
path in `EFFERENTS_HOSTNAME`. The single quotes preserve dollar signs in the
hash when Compose reads `.env`.

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
docker compose logs --tail=50 caddy gateway
```

Open **`https://YOUR-DASHED-IP.sslip.io`** and log in as `organizer` with the
password you chose. The first image build and certificate request can take a
few minutes. This is the first hosted link; the new registry starts empty.

## 5. Run one real experiment without spending tokens

Copy the bundled synthetic lab into persistent storage once, then run one
bounded cycle using canned agent decisions and a real experiment command:

```bash
docker compose exec gateway cp -R /opt/efferents/examples/smoke-lab /data/submissions/smoke-lab
docker compose exec gateway efferents start \
  --submission /data/submissions/smoke-lab --dry-run --max-iterations 1
docker compose exec gateway efferents list
```

Do not repeat the copy over an existing submission. Reload the browser and
select **smoke-coefficient** in Network. Its run ledger should contain one succeeded
run with `synthetic_loss` approximately `0.3`, status stopped, and zero model
spend. This proves the bounded executor, evidence persistence, registry, and
hosted console work together. It is a synthetic plumbing test, not autonomous
scientific discovery.

Check public authentication from your laptop (substitute the real hostname):

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://YOUR-HOSTNAME/api/control
curl --user organizer https://YOUR-HOSTNAME/api/labs
```

The first request must return **401**. The second prompts for your password and
should return the smoke lab. Never expose the site if the first request returns
200. Access the workspace with HTTPS, including for command-line requests.

## 6. Connect and run your own lab

The hosted event profile uses `zai/glm-5.3` for every agent role. To use
Anthropic credits first, set `EFFERENTS_MODEL=claude-sonnet-5,zai/glm-5.3`
in the submission's `.env`. In that configuration, missing credentials or a provider failure
(including exhausted credits) falls through to Z.ai's full GLM-5.3. This is
reactive fallback, not a provider balance lookup. Each new call starts at the
first candidate, so restored Anthropic credits are picked up automatically.
The lab's own daily/lifetime cap still stops requests; it is never bypassed
by fallback. Usage is recorded against the model that actually served it.

Put `ZAI_API_KEY` in the submission's `.env`, alongside `ANTHROPIC_API_KEY`
when available. Use a Z.ai general API account, not a coding-plan endpoint.
The general endpoint is `https://api.z.ai/api/paas/v4`. Restart the lab after
changing its model configuration. Provider keys
are not injected into the web server. See [Z.ai API docs](https://docs.z.ai/api-reference/introduction)
and [pricing](https://docs.z.ai/guides/overview/pricing).

GLM does not provide Anthropic's hosted web-search tool through this adapter;
the existing librarian path synthesizes without that tool. A successful live
research cycle must be verified after adding credentials.

Use your coding agent and the existing [intake](../intake.md) to prepare a
submission containing `README.md`, `lab.yaml`, and a Popper-passed
`hypothesis.md`. For a trusted GitHub repository, paste its repository or README
URL into **Connect → Submit a repo**. It is cloned into the server's persistent
registry directory. A path submitted through the website refers to the server's
filesystem, not your laptop.

For a private local submission, transfer a reviewed source archive to the
Droplet as `/tmp/my-lab.tgz`, excluding credentials and generated lab state.
The archive should contain the submission's files at its root. Extract it as
the gateway user so the lab can write its state and source:

```bash
docker compose exec gateway mkdir -p /data/submissions/MY-LAB
docker compose exec -T gateway tar --no-same-owner -xzf - \
  -C /data/submissions/MY-LAB < /tmp/my-lab.tgz
```

Connect `/data/submissions/MY-LAB` in the browser. Do not transfer your existing
`.env` automatically. Connection validates and initializes the lab; starting
it is a separate action. Install any additional
experiment dependencies in a derived image before running that lab; the base
image contains Efferents and the smoke example only.

Automatic Coder edits require a Git repository with an initial commit and a
configured author. Configure the dedicated lab account once:

```bash
docker compose exec gateway git config --global user.name 'Efferents Lab'
docker compose exec gateway git config --global user.email 'lab@localhost'
```

GitHub clones already have a history. For the copied smoke lab, initialize and
commit only its input files before live execution (runtime state stays out):

```bash
docker compose exec -w /data/submissions/smoke-lab gateway git init
docker compose exec -w /data/submissions/smoke-lab gateway python -c \
  'from pathlib import Path; Path(".gitignore").write_text(".env\n.env.*\nlab/\npopper-corpus/\n__pycache__/\n*.pyc\n")'
docker compose exec -w /data/submissions/smoke-lab gateway git add \
  .gitignore README.md hypothesis.md lab.yaml src configs
docker compose exec -w /data/submissions/smoke-lab gateway git commit -m 'Initialize synthetic lab inputs'
```

For your own copied submission, initialize its Git history with a reviewed
input-file list appropriate to that lab.

Before live research, clone the external Popper Probe dependency separately:

```bash
docker compose exec gateway git clone --depth 1 \
  https://github.com/mashathepotato/popper-probe.git /data/popper-probe
```

`POPPER_PROBE_REPO` is already configured to that path. Record its commit with
`docker compose exec gateway git -C /data/popper-probe rev-parse HEAD` when
recording the environment for a real experiment.

Store provider settings in that **submission's `.env`**, with mode 600. The
start subprocess loads it for the daemon; the web server does not need provider
keys in its environment. For example, to set an Anthropic key for the copied
smoke lab, use this hidden prompt (it refuses to overwrite an existing file):

```bash
docker compose exec gateway python -c '
import getpass, os
from pathlib import Path
key = getpass.getpass("Anthropic API key: ").strip()
if not key or "\n" in key or "\r" in key:
    raise SystemExit("A nonempty, single-line key is required")
p = Path("/data/submissions/smoke-lab/.env")
fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as f:
    f.write("ANTHROPIC_API_KEY=" + key + "\n")
'
```

For another provider or lab, use the appropriate key and model settings from
[the root environment example](../.env.example), at the connected submission's
path. Do not paste credentials into the web hypothesis/steering fields, Git,
image build arguments, or the Caddy configuration. Experiment commands receive
the framework's filtered environment; this is not a sandbox for hostile code.

Set both `budget.daily_cap_usd` and `budget.total_cap_usd` in `lab.yaml` before
live execution. A small trial can use a $1 daily cap and a $2 lifetime cap.
Framework caps are checked at scheduling boundaries, so allow for an in-flight
call; set a provider-side spending limit as well if available. After inspecting
the lab and its budget, start it through the console, then use Steer or Stop.

## Persistence, updates, and scaling

- `/data` is a named Docker volume containing the registry, submissions,
  experiment evidence, and external Popper checkout. Caddy certificates also
  have persistent volumes. Keep the same Compose project name when updating.
- The console and Caddy restart after a server reboot. Detached research daemons
  are **not automatically resumed** after container recreation or reboot.
  Inspect `efferents list` and explicitly restart the labs you intend to fund.
- Before restarting/rebuilding the gateway, stop each running lab with
  `docker compose exec gateway efferents stop --submission /data/submissions/MY-LAB`
  (use the actual registered path), and wait for execution to stop. This keeps
  shutdown and evidence state auditable.
- To update after stopping labs, upload the revised files and run
  `docker compose up -d --build`. To stop the console, use
  `docker compose stop`. Do not add `--volumes` to `down`: it deletes evidence
  and certificate storage.
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

## If the link does not open

Run `docker compose ps` and `docker compose logs --tail=100 caddy gateway` from
`/opt/efferents/deploy/digitalocean`. A gateway container must be healthy; on the
Droplet, `curl http://127.0.0.1:8800/api/labs` should return JSON.

If local JSON works but HTTPS does not, check the hostname's DNS, the attached
firewall's ports 80/443, and Caddy's certificate logs. If sslip.io certificate
issuance is rate-limited, use a hostname under your own domain or try the
equivalent nip.io hostname and recreate Caddy. Keep authentication enabled
while resolving certificate or network issues.

If the browser reports missing credentials, confirm the `.env` is in the
selected submission directory, not its `lab/` directory. If a research run
halts, inspect its recorded halt reason and budget before restarting it.
