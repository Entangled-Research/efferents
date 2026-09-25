# README network demo

`lab-network-demo.gif` was captured from the local Efferents 0.2 console on
2026-09-25. It is a 19-second, 1280×800 looping recording, with captions added
above the unmodified browser captures. The network is shown before and after
visiting an idea's evaluation and an accepted-paper reader.

The isolated examples are numerical methods (two ideas), graph algorithms and
orbital dynamics. Measurements come from three real bounded CPU starter runs
per lab. Publication and review records are **illustrative fixtures**, labeled
in the recording and manuscript. Journal receipts are persisted by the actual
subscription code using an accelerated demonstration clock. Receipt animations
do not claim independent reproduction or new live research.

No event submissions, participant identities, credentials, or production state
are used. Recreate the recording workspace with:

```bash
uv run python scripts/prepare_readme_demo.py /tmp/efferents-readme-demo
EFFERENTS_HOME=/tmp/efferents-readme-demo/home uv run efferents serve
```

Use a fresh destination. Capture the network, an idea evaluation, the numerical
methods publication, then the network again. Preserve the illustrative-record
label when making a new recording. All generated state stays outside the repo.
