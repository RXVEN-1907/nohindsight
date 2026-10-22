"""Generate the vector hero banner from the real demo data.

    python tools/make_hero_svg.py

Reads docs/data/leak_demo.json, writes docs/diagrams/hero.svg. SVG text
renders with system fonts on GitHub, so the type stays crisp at any zoom,
unlike raster text. Deterministic, stdlib only.
"""

from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))
DATA = os.path.join(REPO, "docs", "data", "leak_demo.json")

FONT = "ui-monospace, 'SF Mono', 'Cascadia Code', Menlo, Consolas, monospace"


def main():
    with open(DATA) as f:
        d = json.load(f)
    curve = d["curve"]
    n = len(curve)
    dates = [p["date"] for p in curve]

    W, H = 1400, 420
    X0, X1, Y0, Y1 = 600, 1320, 340, 110
    lo, hi = 96_000, 147_000
    xs = [X0 + (X1 - X0) * i / (n - 1) for i in range(n)]

    def ys(vals):
        return [Y1 + (Y0 - Y1) * (v - lo) / (hi - lo) for v in vals]

    hy, ny = ys([p["honest"] for p in curve]), ys([p["naive"] for p in curve])
    i0 = next(i for i, dt in enumerate(dates) if dt >= "2024-01-02")
    i1 = next(i for i, dt in enumerate(dates) if dt >= "2024-03-01")
    bx0, bx1 = xs[i0], xs[i1]

    s = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" font-family="{FONT}">']
    s.append(f'<rect width="{W}" height="{H}" fill="#0B0C10"/>')
    for gy in (150, 250):
        s.append(f'<line x1="72" y1="{gy}" x2="{W-72}" y2="{gy}" stroke="#161822" stroke-width="1"/>')

    # wordmark
    s.append('<text x="72" y="120" font-size="58" font-weight="bold">'
             '<tspan fill="#D0C4DF">no</tspan><tspan fill="#F2F3F7">hindsight</tspan></text>')
    s.append('<line x1="142" y1="102" x2="512" y2="102" stroke="#E5484D" stroke-width="4" stroke-linecap="round"/>')
    s.append('<text x="74" y="162" fill="#8A8FA3" font-size="16">is your strategy real, or did it cheat</text>')
    s.append('<text x="74" y="184" fill="#8A8FA3" font-size="16">by accident?</text>')
    s.append('<text x="74" y="238" fill="#5A5F72" font-size="13">point-in-time data · leak guards · real costs</text>')
    s.append('<text x="74" y="258" fill="#5A5F72" font-size="13">deflated sharpe · python api · rust engine</text>')

    # chart
    for val in (100_000, 120_000, 140_000):
        y = Y1 + (Y0 - Y1) * (val - lo) / (hi - lo)
        s.append(f'<line x1="{X0}" y1="{y:.1f}" x2="{X1}" y2="{y:.1f}" stroke="#2A2D3A" stroke-width="1"/>')
        s.append(f'<text x="{X0-10}" y="{y+4:.1f}" fill="#4A4E63" font-size="12" text-anchor="end">{val//1000}k</text>')

    s.append(f'<rect x="{bx0:.1f}" y="100" width="{bx1-bx0:.1f}" height="{Y0-100+26}" fill="#E5484D" opacity="0.08"/>')
    s.append(f'<line x1="{bx0:.1f}" y1="100" x2="{bx0:.1f}" y2="{Y0+26}" stroke="#E5484D" stroke-width="1" stroke-dasharray="3 4" opacity="0.6"/>')
    s.append(f'<line x1="{bx1:.1f}" y1="100" x2="{bx1:.1f}" y2="{Y0+26}" stroke="#E5484D" stroke-width="1" stroke-dasharray="3 4" opacity="0.6"/>')
    s.append(f'<text x="{bx0+8:.1f}" y="120" fill="#E5484D" font-size="12.5">the leak window</text>')

    hp = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, hy))
    np_ = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ny))
    s.append(f'<polyline points="{hp}" fill="none" stroke="#D0C4DF" stroke-width="2.4" stroke-linejoin="round"/>')
    s.append(f'<polyline points="{np_}" fill="none" stroke="#E5484D" stroke-width="2.4" stroke-linejoin="round"/>')

    lx = X1 + 14
    s.append(f'<rect x="{lx}" y="{ny[-1]-19:.1f}" width="52" height="22" rx="11" fill="#12141C" stroke="#E5484D" stroke-width="1.4"/>')
    s.append(f'<text x="{lx+26}" y="{ny[-1]-4:.1f}" fill="#E5484D" font-size="12" text-anchor="middle">REAL</text>')
    s.append(f'<rect x="{lx}" y="{hy[-1]+6:.1f}" width="86" height="22" rx="11" fill="#12141C" stroke="#D0C4DF" stroke-width="1.4"/>')
    s.append(f'<text x="{lx+43}" y="{hy[-1]+21:.1f}" fill="#D0C4DF" font-size="12" text-anchor="middle">SUSPICIOUS</text>')

    s.append(f'<text x="{W-72}" y="{H-24}" fill="#4A4E63" font-size="12" text-anchor="end">95 daily samples from one deterministic demo run</text>')
    s.append("</svg>")

    out = os.path.join(REPO, "docs", "diagrams", "hero.svg")
    with open(out, "w") as f:
        f.write("\n".join(s) + "\n")
    print(f"wrote {out} ({os.path.getsize(out)} bytes)")


if __name__ == "__main__":
    main()
