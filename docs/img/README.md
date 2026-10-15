# README product film

`efferents-product-film.mp4` is the assembled 60-second, 1600×900, 30 fps
silent product film. The main README embeds a GitHub video attachment so the
film plays inline. The MP4 here is the versioned source asset; the poster is
retained for other previews.

Current attachment: https://github.com/user-attachments/assets/b4d596cc-f67b-49cc-b007-2088a81854bb

After rendering a replacement, upload the new MP4 through GitHub’s Markdown
editor attachment control and update the standalone attachment URL in the main
README. Repository file links do not produce GitHub’s inline video player.

The opening captures the actual merged dashboard HTML, stylesheet, lab
boundaries, measured idea layouts, review gates, and journal paths. Its camera
and illustrative fixtures live in `../film/network-study.js`. The remaining
sections use the original film's canvas source, `../film/product-film.html`.
The build retimes those sections, joins the opening with a 0.4-second dissolve,
and produces exactly one minute of video. The network section remains 15 seconds.

| Time | Sequence |
| --- | --- |
| 0–15s | Canonical solid lab network: pullback, 30 identities, staggered arrivals and retirements, publication and reading flows |
| 14.6–23s | Transition from the selected Numerical methods lab into its ideas |
| 23–33s | Eval suite, measurement history, and failed run retained in the ledger |
| 33–45s | Owner steers toward oscillatory-function tests with a $5 cap; supervisor queues suite v2 |
| 45–55s | Admin concept: costs, limits, experiments, paper outcomes, reproductions, next commitment |
| 55–60s | Closing product statement |

All measurements, costs, publications, reproductions, and lifecycle events are
**illustrative authored data**, not a recording of live research. The admin
screen remains a **design concept**. Steering queues future work; it does not
invent subsequent results. Cross-lab scientific exchange follows the actual
UI's accepted-publication and recorded-reading paths through journals.

Build the complete film with Node.js, `playwright`, installed Chrome, and
`ffmpeg`:

```bash
NODE_PATH=/path/to/node_modules node scripts/build_product_film.cjs
```

The build renders intermediate clips in a fresh temporary directory, verifies
that the final video decodes, and updates the main MP4 and poster. The source
renderers remain available for individual section work:

```bash
NODE_PATH=/path/to/node_modules node scripts/render_network_film.cjs
NODE_PATH=/path/to/node_modules node scripts/render_product_film.cjs
```

`render_product_film.cjs` alone renders the original 65-second canvas version;
use `build_product_film.cjs` for the current assembled README film. The original
recorded GIF is retained below as a reference.

## Next iteration: network motion study

The main README film now includes the 15-second canonical UI opening. The
separate `efferents-network-study.mp4` remains an earlier standalone study; its fixture and camera source is `../film/network-study.js`.

This 15-second study matches the original film’s network section while using 30 lab identities, 10 staggered arrivals, four
retirements, and more than 90 ideas in the final active network. Retirement
shrinks a lab out of the active map while preserving its evidence. The camera
pulls back from one lab, observes network activity, then follows an accepted
result through a journal into another lab's next question.

Connection semantics follow the merged canonical UI (`d1da507`,
`dashboard.js` functions `networkFlowRecords` and `renderNetwork`): teal
accepted-publication flow, cobalt recorded reading, dotted occasional
cross-journal reading, and terracotta revision returns. Many ideas exchange
reviewed scientific output via journals; receipt is distinct from reproduction.
All lifecycle and exchange events remain illustrative authored fixtures.

The study captures the actual merged dashboard HTML, CSS, and JavaScript; it
does not redraw lab cards in canvas. The renderer also produces an offline
preview at `/tmp/efferents-network-ui-preview.html`.

Render separately without replacing the approved film or its poster:

```bash
NODE_PATH=/path/to/node_modules node scripts/render_network_film.cjs
```

## Earlier recorded demo

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
