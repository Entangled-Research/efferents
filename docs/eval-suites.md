# Lab evaluation suites

`efferents evals generate --submission PATH` makes one budgeted model call through
the lab's existing provider settings. Event participants use their own proxy token;
the upstream Azure key stays on the hub. The command writes `eval-suite.json` with
lab-specific metric histories and image artifact kinds, validates column references,
and records model and input/output hashes in `context/eval-suites/`. Existing suites
are reused. `--replace` archives the previous suite before regeneration.

The lab author must implement the metrics, baseline, relevant uncertainty, and
sample/error galleries in the real executor. Declare metrics in `metrics.panels`
and emit PNG artifact paths in the result envelope. Use unique filenames. Model
generation configures views; it cannot invent measurements or execute new code.
Run smoke, inspect the emitted files, then run `efferents evals validate`. Smoke
measurements should have an explicit eligibility constraint excluding them from
scientific conclusions. Viewing the console makes no model calls.

Joined event labs generate a missing suite at model-driven daemon startup and
validate existing suites before execution. Standalone labs can opt in with
`EFFERENTS_GENERATE_EVAL_SUITE=1`. Generation failure blocks startup with a
diagnostic. Offline starter trials remain available without a model key.

Eval plots use persisted eligible runs, and hover labels identify the source run.
Missing suites and missing samples remain visible as missing; they are not scored
as successful evaluations. Existing evidence and run artifacts are retained.
