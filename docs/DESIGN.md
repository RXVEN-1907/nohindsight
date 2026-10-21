# nohindsight — design tokens

One rule above all: **lavender is the truth, crimson is the leak.**
Lavender marks what the world actually knew. Crimson is reserved exclusively
for flagged data, leaks, and fantasy verdicts — it must never appear as
decoration. If something is crimson, a quant should feel accused.

| Element              | Dark Mode 🌙             | Light Mode ☀️            |
| -------------------- | ------------------------ | ------------------------ |
| Canvas / BG          | `#0B0C10` (Obsidian Ink) | `#FAF9FB` (Paper White)  |
| Primary text         | `#F2F3F7` (Off-White)    | `#14161D` (Charcoal)     |
| The Truth / Baseline | lavender `#D0C4DF`       | lavender `#705EB1`       |
| The Leak / Flagged   | crimson `#E5484D`, sulfur `#E6B455`, or blue `#5B8DEF` | same |

Secondary tokens: panels `#12141C`, hairlines `#2A2D3A`, muted text
`#8A8FA3`, ghost text `#4A4E63`, lavender-deep `#705EB1`.

Severity mapping (used by `report.py` and every diagram):

- `LEAK` → crimson `#E5484D`
- `WARN` → sulfur `#E6B455`
- `INFO` → blue `#5B8DEF`
- baseline / honest / REAL verdict → lavender `#D0C4DF`

Dark mode is the default. Light mode exists for print and paper-white
terminals; the hue roles never change, only the canvas.

Typography: system font stack for prose, monospace for anything that is a
number, a code identifier, or a diagram label. Numbers get monospace because
a quant reads numbers, and they should look like data, not prose.

Diagrams live in `docs/diagrams/` as hand-set SVG: dark canvas embedded,
no external fonts, no raster images. Each diagram is placed asymmetrically
on purpose — this repo is about seeing clearly, not about decorating.
