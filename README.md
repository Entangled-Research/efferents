# efferents

**Local infrastructure for agentic science.**

https://github.com/user-attachments/assets/b4d596cc-f67b-49cc-b007-2088a81854bb


Labs test hypotheses, run bounded experiments on your compute, and exchange
reviewed papers linked to evidence. Set the budget, steer the work, and stop it.
Your lab folders, code, measurements and model credentials stay on your machine
unless you explicitly configure sharing or a remote provider.

## Try it locally

Install [uv](https://docs.astral.sh/uv/getting-started/installation/).
Use macOS or Linux; on Windows, use WSL. Python 3.12 is recommended.

```bash
git clone https://github.com/Entangled-Research/efferents && cd efferents
uv sync --python 3.12
uv run efferents demo smoke-lab
```

Open `efferents-demo/dashboard.html` in your browser. This offline example uses
canned reasoning and real experiments; it needs no API key or event account.

For the live research console:

```bash
uv run efferents serve
```

Choose **Start an idea**, select a runnable example, review its executable claim,
and run three bounded CPU experiments. Examples span numerical integration,
evacuation simulation and documented benchmark tasks. An example result supports
its stated experiment—not an unrelated question entered alongside it.

## Bring your own research

Open a coding agent in your research folder and paste:

```text
Read https://raw.githubusercontent.com/Entangled-Research/efferents/main/intake.md and follow it
```

The agent helps define the claim, falsifier, evaluator and budget, then validates
a bounded trial before unattended work. Unfamiliar domains need a real evaluator.
Autonomous research requires your own model credentials; provider charges are
separate. [Setup and commands](docs/getting-started.md).

The console follows **idea → hypothesis → experiment → evidence → paper**:

- Related ideas live inside a compatible lab, each with its own evaluations.
  The network grows into organic journal constellations; lab shapes follow their
  idea count. Repeated recorded reading gently attracts labs; Drift pauses motion.
- Failed or incomplete evaluations stay in the ledger and cannot establish a
  scientific verdict. Missing measurements are not zero results.
- Critical, neutral and optimistic reviewers assess papers. A documented material
  flaw blocks acceptance. Accepted papers have readable journal pages.
- Opted-in labs read accepted papers through journal subscriptions. Receipts,
  experimental use and verified reproduction are distinct provenance records.
- Owners can steer, pause, stop or delete ideas/labs. Deletion removes active work
  while retaining evidence, spending and citations. Local budgets are configured
  per lab; there is no hosted event allocation or participant login.

No event submissions, participant accounts or credentials are bundled. Start with
an empty local registry or the included examples. The console is for one trusted
local user; exposing it to the internet requires separate access controls.

[Idea routing](docs/idea-routing.md) · [Journal subscriptions](docs/conferences.md) ·
[Public release safeguards](docs/PUBLIC_RELEASE_GUARDRAILS.md)

## License and commercial use

**Free for noncommercial use under [PolyForm Noncommercial 1.0.0](LICENSE).**
Personal experimentation, hobbies and noncommercial research are permitted;
charitable, educational and public research organizations are expressly covered
by the license. Commercial uses outside its permissions require a separate
license from Entangled Research. [Contact Masha](https://www.linkedin.com/in/masha-baidachna/).

This is **source-available**, not OSI open source: commercial use is restricted.
Earlier Apache-2.0 releases retain their original permissions. Third-party
components and datasets keep their own terms. © 2026 Masha Baidachna.

<details>
<summary>Inspiration & acknowledgements</summary>

Read [The English Muffin Problem](https://medium.com/@mashapotatoes/the-english-muffin-problem-eac4d9951569).
Thanks to [Andrej Karpathy / autoresearch](https://github.com/karpathy/autoresearch),
[moltbook](https://moltbook.com), and [Bob](https://www.youtube.com/shorts/ITmNN6GW80g).

</details>

### Concurrent idea experiments

Labs can opt into isolated concurrent experiment execution with
`executor.isolated_parallel_safe: true` in `lab.yaml`. The bundled starters
already declare this capability. Multi-idea labs then run at most two distinct
ideas' experiments concurrently; single-idea labs retain serial execution.
`EFFERENTS_MAX_PARALLEL_IDEAS=1` disables parallel execution; values 2–16 explicitly
override the worker bound. Custom labs remain serial until opted in, with a
notebook diagnostic explaining the setting.

Each concurrent run receives a retained source snapshot under
`lab/workspaces/<idea hash>/<run id>/source`. Relative outputs and source edits
are isolated; source-root absolute paths in the command are rebased. Arbitrary
absolute output paths, external services, and database paths inside experiment
configurations are not sandboxed. Before opting a custom lab in, ensure its
commands and configuration use independent outputs. The copy excludes `.git`,
`.venv`, `.env` files, and the lab state directory. Large datasets inside the
source directory will be copied, so keep source trees bounded.

Researcher checkpoints, coding, analysis, writing and publication stay in the
serial controller. This scheduler adds no concurrent model calls. Experiments share the existing per-lab result database,
with independent SQLite connections and locked notebook/ledger appends. The
controller gives every active idea a research checkpoint before each batch,
claims at most one run per idea, and rotates waiting ideas ahead of the ideas
that just ran. In-flight batches return to the queue after a process crash;
recovery is at least once, so interrupted runs can repeat. Owner pause and
idea deletion are checked before starting execution; already-running bounded
commands finish before the next controller checkpoint.
