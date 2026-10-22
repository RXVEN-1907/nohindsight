"""Build README.md: eight stops, one visual system.

    python tools/build_readme.py

Vector hero from docs/diagrams/hero.svg, animation and stills from
docs/graphics, five mermaid diagrams, and the complete source of every
artifact type inlined into one consolidated gallery section. Stdlib only.
"""

from __future__ import annotations

import os

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
GALLERY_COMMIT = "2df2423"  # last commit that carried the gallery sources


def inline(path, fence="````"):
    """Read a source file; gallery sources live only in git history now."""
    full = os.path.join(REPO, path)
    if os.path.exists(full):
        body = open(full).read().rstrip("\n")
    else:
        import subprocess
        r = subprocess.run(
            ["git", "-C", REPO, "show", f"{GALLERY_COMMIT}:{path}"],
            capture_output=True, text=True, check=True,
        )
        body = r.stdout.rstrip("\n")
    return f"{fence}{os.path.splitext(path)[1][1:]}\n{body}\n{fence}"


def details(summary, body):
    return f"<details>\n<summary>{summary}</summary>\n\n{body}\n\n</details>"


def main():
    t = """<p align="center">
  <img src="docs/diagrams/hero.svg" alt="nohindsight: both equity curves, the leak window, the verdicts" width="100%">
</p>

[![ci](https://github.com/RXVEN-1907/nohindsight/actions/workflows/ci.yml/badge.svg)](https://github.com/RXVEN-1907/nohindsight/actions/workflows/ci.yml)
[![rust](https://github.com/RXVEN-1907/nohindsight/actions/workflows/rust.yml/badge.svg)](https://github.com/RXVEN-1907/nohindsight/actions/workflows/rust.yml)
[![license](https://img.shields.io/badge/license-MIT-705EB1)](LICENSE)
[![dependencies](https://img.shields.io/badge/dependencies-zero-0B0C10?labelColor=12141C)](Cargo.toml)
[![python](https://img.shields.io/badge/python-3.10%2B-705EB1)](pyproject.toml)
[![rust](https://img.shields.io/badge/rust-1.75%2B-705EB1)](Cargo.toml)

> A quant toolkit built around one question: is your strategy real, or did it cheat by accident?

Not a backtester. The layer around one: data that records when it became
knowable, scanners for the standard cheats, costs priced from market
structure, statistics that discount luck, a risk layer for live use.
Python research API, Rust engine, both dependency-free.

---

## The demo, in 30 seconds

Two identical strategies run through the same engine, same broker, same
fees. They differ in one line: how they read a restated revenue figure.
The naive one applies today's number to every historical date, which is
what a casual pandas merge does silently.

<p align="center">
  <img src="docs/graphics/leak.gif" alt="animated demo: honest and naive equity diverge inside the leak window" width="820">
</p>

| | verdict | sharpe | permutation p |
|---|---|---|---|
| honest (guarded read) | SUSPICIOUS | 0.69 | 0.111 |
| naive (leaking read) | REAL | 1.17 | 0.020 |

Leak accounting: 43 decision days priced off a revision first published
2024-03-01, first read 2024-01-02, up to 58 days before the world had it.

<p align="center">
  <img src="docs/graphics/receipt.svg" alt="the evidence board" width="720">
</p>

```bash
git clone https://github.com/RXVEN-1907/nohindsight && cd nohindsight
python -m nohindsight demo          # the research API
cargo run --release -- demo         # the engine, same story
```

---

## How the guard works

Every record carries two timestamps. `event_time` is when the thing
happened. `knowledge_time` is when an honest observer could have first
known it. A backtest on 2024-02-15 that reads the restated 177.4, first
knowable 2024-03-01, has leaked, and leaks always flatter the result.

<p align="center">
  <img src="docs/diagrams/two-clocks.svg" alt="event_time vs knowledge_time" width="820">
</p>

```python
from nohindsight import Clock, Ledger

ledger = Ledger("fundamentals")
ledger.append("SYM2.revenue", 253.4, "2023-12-31", "2024-02-09")  # first filing
ledger.append("SYM2.revenue", 177.4, "2023-12-31", "2024-03-01")  # restatement

view = ledger.view(Clock("2024-02-15"))
view.get("SYM2.revenue")   # 253.4: latest revision knowable on that date
```

```mermaid
sequenceDiagram
    participant S as strategy
    participant V as AsOfView (clock at 2024-02-15)
    participant L as Ledger
    S->>V: get("SYM2.revenue")
    V->>L: latest revision with knowledge_time <= now
    L-->>V: rev 0, known 2024-02-09, value 253.4
    V-->>S: 253.4
    note over S,V: naive path: ledger.value_at(key, terminal)<br/>returns 177.4, a 14 day leak, silently
```

---

## The departments

```mermaid
flowchart LR
    D[data: point-in-time ledgers] --> G{{guard: AsOfView}}
    G --> S[strategy on a Clock]
    S --> C[costs: fees, spread, impact]
    C --> F[forensics: DSR, PBO, permutation]
    K[checks: scans data and code] -.findings.-> D
    K -.findings.-> S
    F --> L[live: paper broker, kill switch]
    L -.same code path.-> D
    classDef data fill:#12141C,stroke:#705EB1,color:#F2F3F7
    classDef guard fill:#1C1626,stroke:#D0C4DF,color:#D0C4DF
    classDef plain fill:#12141C,stroke:#5A5F72,color:#F2F3F7
    classDef warn fill:#12141C,stroke:#E6B455,color:#F2F3F7
    class D data
    class G guard
    class S,C,F,L plain
    class K warn
```

| | | |
|---|---|---|
| **data** | append-only revisioned ledgers, universes with ghosts, EDGAR and ALFRED adapters | catches restatements applied early, survivorship |
| **checks** | OHLC integrity, gaps, stale bars, duplicates, crossed quotes, split detector, AST lint for future-reading code | catches dirty bars and time travel |
| **costs** | fees, spread, borrow, square-root impact `sigma * sqrt(qty / ADV)` | prices the flat-slippage fantasy |
| **forensics** | Probabilistic and Deflated Sharpe, PBO via CSCV, permutation tests | catches selection on luck |
| **live** | decimal-exact paper broker, deterministic fill emulator, latching kill switch | catches the first live disaster |

The demo plants five real defect classes: an unadjusted 4:1 split, a
nine-day gap, five stale closes, a duplicated timestamp, and the
restatement. `checks` catches four from the bars alone. The fifth is only
visible through the two-clock model, which is the point.

---

## Forensics

<p align="center">
  <img src="docs/diagrams/forensics-gauntlet.svg" alt="the gauntlet a sharpe must survive" width="800">
</p>

```python
from nohindsight.forensics import audit

v = audit(returns, n_trials=40)   # count the configs you tried, honestly
v.level       # REAL | SUSPICIOUS | FANTASY
v.reasons     # sentences citing the actual numbers
```

The best of 40 trials is positive under no edge, so the benchmark is not
zero. The audit computes that hurdle, tests against it with skew and
kurtosis included, cross-validates the winner out-of-sample (PBO), and
compares against a sign-flipped null. REAL: DSR probability at 0.95 or
higher, p under 0.05, PBO under 0.2.

---

## The same leak, every medium

Every graphic on this page is generated from one deterministic run
(`docs/data/leak_demo.json`, 95 daily samples of both curves). Regenerate
everything with `python tools/graphics.py` and `python tools/build_readme.py`.

<p align="center">
  <img src="docs/graphics/scene3d.png" alt="both equity curves as 3d ribbons over the leak plane" width="860">
</p>
<p align="center">
  <sub>numpy painter, pinhole camera, no GPU, no renderer dependency</sub>
</p>

<p align="center">
  <img src="docs/graphics/leak_pulse.svg" alt="animated: the naive read reaches into the future" width="800">
</p>
<p align="center">
  <sub>SMIL animation, loops on this page, a few kilobytes</sub>
</p>

The full video with the sonified soundtrack:
[docs/graphics/nohindsight_leak.mp4](docs/graphics/nohindsight_leak.mp4)
(1080p, 12 s). During the leak window the naive voice detunes against the
honest one, so the cheat is audible before it is visible.

{GALLERY_DETAILS}

---

## The syllabus

`docs/MILESTONES.md`: five milestones from "find one wrong thing in a CSV"
to "fit the square-root law on real crypto tapes" to "watch fifty lucky
monkeys get deflated". Each is one concept interviewers ask about and one
demoable artifact.

## Limitations

- Fill simulation is bar-close plus latency drift; no queue-position
  modeling yet.
- PBO samples CSCV combinations above 512; seeds are documented.
- EDGAR and ALFRED adapters are offline-safe skeletons, tested on fixtures.
- The runtime leak probe cannot see fixed-timestamp peeks; the AST lint
  catches those. Neither substitutes for the guard.
- The ONNX export is structurally validated, not numerically compared
  against torch output yet.

## Status

v0.1.0. 162 Python tests, 94 Rust tests, both suites green in CI, zero
dependencies in both languages. Build spec: `docs/SPEC.md`. Design tokens:
`docs/DESIGN.md`.

MIT. Built to learn in public.
"""

    gallery = "\n\n".join([
        details("matplotlib · the two curves as a static HD chart", inline("gallery/media/plot_equity.py")),
        details("Chart.js · the curves, interactive, self-contained HTML", inline("gallery/media/equity_chart.html")),
        details("mermaid xychart · the curves in pure text", '''```mermaid
xychart-beta
    title "equity: honest vs naive (USD)"
    x-axis ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec", "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    y-axis "USD" 90000 --> 150000
    line "naive" [100000, 101000, 98000, 104000, 107000, 111000, 114000, 118000, 121000, 125000, 128000, 131000, 133000, 136000, 139000, 141000, 143000, 144000, 144000, 144000, 144000, 144000, 144000, 144000]
    line "honest" [100000, 101000, 100000, 102000, 103000, 105000, 106000, 108000, 109000, 111000, 112000, 114000, 115000, 117000, 118000, 119000, 120000, 121000, 121000, 122000, 122000, 122000, 122000, 122000]
```'''),
        details("Three.js · the ribbons scene, draggable, self-contained HTML", inline("gallery/media/equity_3d.html")),
        details("manim · the two clocks scene, renders to mp4", inline("gallery/media/manim_two_clocks.py")),
        details("CSS keyframes · the leak pulse, zero JavaScript", inline("gallery/media/leak_pulse.html")),
        details("ffmpeg · mux the frames with the sonified audio", inline("gallery/audio/ffmpeg_splice.sh")),
        details("sonification · stdlib WAV writer, three files", inline("gallery/audio/sonify.py")),
        details("MIDI · a hand-rolled Standard MIDI File with a parse-back verifier", inline("gallery/audio/midi_map.py")),
        details("librosa · where the spectral centroid moves: the reveal", inline("gallery/audio/analyze_leak.py")),
        details("SVG source · the two clocks diagram", inline("docs/diagrams/two-clocks.svg")),
        details("SVG source · the departments architecture", inline("docs/diagrams/architecture.svg")),
        details("SVG source · the field guide to six species", inline("docs/diagrams/leak-taxonomy.svg")),
        details("mindmap · the six species as a mermaid map", '''```mermaid
mindmap
  root((cheating by accident))
    the time traveler
      look-ahead bias
      caught by: the guard and the AST lint
    the ghost list
      survivorship bias
      caught by: universe membership
    the moving number
      restatements
      caught by: revisioned ledgers
    the lucky monkey
      overfitting
      caught by: deflated sharpe and PBO
    the free lunch
      flat slippage assumptions
      priced by: square-root impact
    the shrinking pond
      universe drift
      caught by: point-in-time membership
```'''),
        details("OpenSCAD · the printed case for the kill-switch Pi", inline("gallery/infra/case.scad")),
        details("Godot 4 · the market room, procedural columns", inline("gallery/infra/market_room.gd")),
        details("Raspberry Pi · the kill-switch daemon, gpiozero", inline("gallery/infra/killswitch_pi.py")),
        details("Wireshark and nginx · watching broker traffic, serving reports", inline("gallery/infra/watch_rules.md")),
        details("Terraform · the report bucket and the nightly audit runner", inline("gallery/infra/main.tf")),
        details("OpenSSL · Ed25519 sign and verify a report artifact", inline("gallery/infra/gen_keys.sh")),
        details("PostgreSQL · point-in-time seeds and the leak-catching query", inline("gallery/infra/seeds.sql")),
        details("PyTorch and ONNX · the leak classifier, accuracy 1.0000 on 2048 windows", inline("gallery/infra/leak_classifier.py")),
    ])
    t = t.replace("{GALLERY_DETAILS}", gallery)

    out = os.path.join(REPO, "README.md")
    with open(out, "w") as f:
        f.write(t)
    print(f"wrote {out}: {len(t)} bytes, {t.count(chr(10))} lines")
    print("sections:", t.count("\n## "))
    print("details blocks:", t.count("<details>"))
    print("mermaid blocks:", t.count("```mermaid"))
    print("images:", t.count("<img "))
    print("em-dashes:", t.count(chr(0x2014)))


if __name__ == "__main__":
    main()
