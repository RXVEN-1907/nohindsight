# design tokens

Canvas, text, and accent colors used by the diagrams, the HTML report, and
every gallery artifact. Dark is the default. Light exists for print.

| Element              | Dark Mode | Light Mode |
| -------------------- | --------- | ---------- |
| Canvas / BG          | `#0B0C10` | `#FAF9FB`  |
| Primary text         | `#F2F3F7` | `#14161D`  |
| Baseline / honest    | `#D0C4DF` | `#705EB1`  |
| Leak / flagged       | `#E5484D` | `#E5484D`  |
| Warn                 | `#E6B455` | `#E6B455`  |
| Info                 | `#5B8DEF` | `#5B8DEF`  |

Secondary values: panel `#12141C`, hairline `#2A2D3A`, muted text
`#8A8FA3`, ghost text `#4A4E63`, deep lavender `#705EB1`.

Severity mapping, used by `report.py` and the diagrams:

- `LEAK` uses `#E5484D`
- `WARN` uses `#E6B455`
- `INFO` uses `#5B8DEF`
- baseline, honest series, and the `REAL` verdict use `#D0C4DF`

Crimson marks flagged data. It does not appear as a decoration color.

Typography: system font stack for prose, monospace for numbers, code
identifiers, and diagram labels.

Diagrams live in `docs/diagrams/` as hand-set SVG with the dark canvas
embedded, no external fonts, no raster images.
