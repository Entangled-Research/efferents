# Idea evaluation suites

Opening a lab displays its idea roster. An idea opens at
`#observe/<lab_id>/idea/<student_id>` and reads
`/api/labs/<lab_id>/ideas/<student_id>`.

Each idea can declare `ideas/<student_id>/eval-suite.json`: `version: 1`,
`title`, `rationale`, and the existing lab contract sections `metrics`,
`falsifiers`, and `evidence`. Optional `graphs` contain a title and metric
column list; `implementation_note` explains readiness. This is a declarative
read-only evaluation view, not executable code or an experiment launch.

For backward compatibility only the default idea inherits the original
lab contract and root `eval-suite.json`. New ideas without a contract show
an unconfigured suite, never the default idea's metrics or results.

Runs are attributed by student ID and campaign ownership before limiting
results. Conflicting attribution is excluded. Hypotheses, metrics, best runs,
falsifiers and artifacts all use that same idea scope. Deployment images
without run attribution are not included in idea suites. Shared paper and
notebook lists are omitted rather than falsely assigned to an idea.

Event sync carries separate idea bundles inside the bounded owner-eval
snapshot. Detailed results retain the existing owner-only access rule;
shared idea rosters may describe their evaluation protocols without sharing
measurements. Unknown ideas return 404. No default-idea fallback is used when
an idea snapshot is missing.
