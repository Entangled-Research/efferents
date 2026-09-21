# Event model funding and Azure decision

**Recorded:** 2026-09-16. **Source:** owner update in the active implementation task.

The owner has **$5,000 in Microsoft for Startups Azure credits**, with the
portal showing a **June 9, 2027** expiration, and wants the event to use
Azure-hosted OpenAI models of different capability/cost tiers. This supersedes
the earlier event-provider decision in the operator plan. It does not
change non-event lab defaults or authorize spending the whole credit balance.

The first event uses three organizer-controlled, Azure OpenAI v1 Chat
Completions deployments: **GPT-4.1 nano** (`fast`), **GPT-5.6 Luna**
(`standard`), and **GPT-5.6 Sol** (`deep`). The owner explicitly chose Luna for
cost-sensitive work such as literature review, Sol for coding/challenging work,
and GPT-4.1 nano for the fastest calls. Fast is for routing; Luna is for
student/researcher, librarian, writer, and most review work; Sol is for
supervisor, analyst, and coder. The current lab client speaks Chat Completions.
For Luna/Sol, the event proxy translates `max_tokens` to
`max_completion_tokens`. On calls with function tools it sets
`reasoning_effort=none` to satisfy Azure GPT-5.6 Chat Completions constraints;
calls without tools retain medium reasoning. Full reasoning-plus-tools would
require a separately tested Responses API migration. The proxy also limits
Luna/Sol request bodies to 200 KB to avoid the long-context pricing tier.
This is a deployment decision, not a claim of subscription access. The owner must verify
region, deployment availability, model version, quota, credit eligibility, and
actual Azure price before deploying the three names. Do not use a
provisioned-throughput deployment or a partner-billed model by accident.
Some subscriptions need a GPT-5.6 quota request; no live access is confirmed.

The public event service accepts only those three aliases and holds the Azure
resource key server-side. The participant credential contains only an opaque,
capped event token and public per-tier prices for local budget estimates. The
event service maintains separate per-lab and total dollar caps. Event limits
remain deliberately much lower than the $5,000 credit allocation until a
real two-laptop and venue rehearsal justifies a change. Azure budgets alert;
they do not stop usage, so they are not a substitute for the proxy caps.

The credits are an account fact supplied by the owner, not verified by code.
No Azure resource, deployment, subscription, billing meter, or successful API
call was available to this task. Record the offer type, expiry, and remaining
balance privately in the operator's billing console, not in this repository.
