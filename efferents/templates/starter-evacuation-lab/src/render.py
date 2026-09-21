"""Dependency-free SVG evidence renderer."""
from __future__ import annotations

import html
from pathlib import Path


def render_svg(layout, baseline, candidate, path: Path) -> None:
    cell = 14
    gap = 28
    panel_w = layout.width * cell
    panel_h = layout.height * cell
    width = panel_w * 2 + gap + 32
    height = panel_h + 70
    pieces = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#faf8f0"/>',
        '<style>text{font-family:monospace;fill:#071a33}.wall{fill:#071a33}.exit{fill:#d4a017}.path{fill:none;stroke-width:.65;opacity:.28}</style>',
        '<text x="16" y="20" font-size="11">PAIRED SYNTHETIC EVIDENCE · SAME LAYOUT / STARTS</text>',
    ]
    for panel, result in enumerate((baseline, candidate)):
        ox = 16 + panel * (panel_w + gap)
        oy = 42
        pieces.append(f'<text x="{ox}" y="34" font-size="10">{html.escape(result.policy.upper())} · median {result.median_steps:g} · completion {result.completion_rate:.3f}</text>')
        pieces.append(f'<rect x="{ox}" y="{oy}" width="{panel_w}" height="{panel_h}" fill="#f3efe3" stroke="#33465f"/>')
        for x, y in sorted(layout.walls):
            pieces.append(f'<rect class="wall" x="{ox + x*cell}" y="{oy + y*cell}" width="{cell}" height="{cell}"/>')
        for x, y in layout.exits:
            pieces.append(f'<rect class="exit" x="{ox + x*cell}" y="{oy + y*cell}" width="{cell}" height="{cell}"/>')
        colors = ("#003b80", "#a8502b", "#2d5379", "#c05a2e")
        for index, trajectory in enumerate(result.trajectories):
            points = " ".join(
                f"{ox + x*cell + cell/2:g},{oy + y*cell + cell/2:g}" for x, y in trajectory
            )
            pieces.append(f'<polyline class="path" stroke="{colors[index % len(colors)]}" points="{points}"/>')
    pieces.append(f'<text x="16" y="{height - 12}" font-size="9">layout sha256:{layout.layout_hash}</text>')
    pieces.append("</svg>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(pieces) + "\n", encoding="utf-8")
