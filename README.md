# efferents

**Turn a research idea or repository into a local autonomous lab.**

Test hypotheses, run bounded experiments on your own compute, and inspect
research memos linked to evidence. Set the budget, steer the work, and stop it.
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
  The network grows into a grid; labs expand to show their idea branches.
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
