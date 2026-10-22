"""Build README.md: the file is the demo reel.

    python tools/build_readme.py

Assembles the README from HD graphics (docs/graphics), SVG diagrams
(docs/diagrams), mermaid blocks, and the complete source of every artifact
type, inlined into <details> blocks. Reads gallery/ sources before they are
stripped from the repo; after that this script and the README stand alone.
Stdlib only. No em-dashes. No ASCII art.
"""

from __future__ import annotations

import os

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def inline(path, fence="````"):
    body = open(os.path.join(REPO, path)).read().rstrip("\n")
    return f"{fence}{os.path.splitext(path)[1][1:]}\n{body}\n{fence}"


def details(summary, body):
    return f"<details>\n<summary>{summary}</summary>\n\n{body}\n\n</details>"


def main():
    template = """# **no**~~hindsight~~

> is your strategy real, or did it cheat by accident?

[![ci](https://github.com/RXVEN-1907/nohindsight/actions/workflows/ci.yml/badge.svg)](https://github.com/RXVEN-1907/nohindsight/actions/workflows/ci.yml)
[![rust](https://github.com/RXVEN-1907/nohindsight/actions/workflows/rust.yml/badge.svg)](https://github.com/RXVEN-1907/nohindsight/actions/workflows/rust.yml)
[![license](https://img.shields.io/badge/license-MIT-705EB1)](LICENSE)
[![dependencies](https://img.shields.io/badge/dependencies-zero-0B0C10?labelColor=12141C)](Cargo.toml)
[![python](https://img.shields.io/badge/python-3.10%2B-705EB1)](pyproject.toml)
[![rust](https://img.shields.io/badge/rust-1.75%2B-705EB1)](Cargo.toml)

A quant toolkit built around one question. Not a backtester: the layer
around one. Data that records when it became knowable, scanners for the
standard cheats, costs priced from market structure, statistics that
discount luck, a risk layer for live use. Python research API, Rust
engine, both dependency-free. Every graphic on this page is generated from
one deterministic demo run, and every generator ships in this file.

<p align="center">
  <img src="docs/graphics/hero.png" alt="both equity curves, the leak band, the verdicts" width="100%">
</p>

## The demo, animated

Two identical strategies run through the same engine, same broker, same
fees. They differ in one line: how they read a restated revenue figure.
The naive one applies today's number to every historical date, which is
what a casual pandas merge does silently. Watch where the extra return
comes from.

<p align="center">
  <img src="docs/graphics/leak.gif" alt="animated demo: honest and naive equity, the leak band, the verdicts" width="860">
</p>

| | verdict | sharpe | permutation p |
|---|---|---|---|
| honest (guarded read) | SUSPICIOUS | 0.69 | 0.111 |
| naive (leaking read) | REAL | 1.17 | 0.020 |

Leak accounting: 43 decision days priced off a revision first published
2024-03-01, first read 2024-01-02, up to 58 days before the world had it.
The naive strategy made more money because it read a number the world did
not have yet.

<p align="center">
  <img src="docs/graphics/receipt.svg" alt="the evidence board" width="740">
</p>

Run it yourself: `python -m nohindsight demo` (research API) or
`cargo run --release -- demo` (the engine). Same story, both languages.

---

## The two clocks

Every record carries two timestamps: `event_time`, when the thing happened,
and `knowledge_time`, when an honest observer could have first known it. A
fundamental for Q4 2023 happened on 2023-12-31, became known 2024-02-09,
and was restated 2024-03-01. A backtest on 2024-02-15 must see 253.4.
Reading 177.4 there is the most common way a backtest lies, and it always
makes the result look better.

<p align="center">
  <img src="docs/diagrams/two-clocks.svg" alt="event_time vs knowledge_time" width="860">
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

| Department | What it does | What it catches |
|---|---|---|
| `data` | Append-only revisioned ledgers; universes with delisted ghosts; SEC EDGAR and ALFRED vintage adapters | restatements applied early, survivorship |
| `checks` | OHLC integrity, gaps, stale bars, duplicates, crossed quotes, a split detector, an AST lint for future-reading code | dirty bars, `shift(-1)` habits |
| `costs` | Fee schedules, spread, borrow, square-root impact: `bps = coeff * sigma * sqrt(qty / ADV)` | the flat-slippage fantasy |
| `forensics` | Probabilistic Sharpe, Deflated Sharpe (Bailey and Lopez de Prado, 2014), PBO via CSCV, permutation tests | selection on luck |
| `live` | Decimal-exact paper broker, deterministic fill emulator, a latching kill switch | the first live disaster |

The five planted defects are real defect classes: a 4:1 split left
unadjusted, a nine-day gap, five stale closes, a duplicated timestamp, and
the restatement. `checks` catches four from the bars alone. The fifth is
only visible through the two-clock model, which is the point.

{MINDMAP}

---

## What forensics does to a Sharpe

<p align="center">
  <img src="docs/diagrams/forensics-gauntlet.svg" alt="the gauntlet a sharpe must survive" width="820">
</p>

```python
from nohindsight.forensics import audit

v = audit(returns, n_trials=40)   # count the configs you tried, honestly
v.level       # REAL | SUSPICIOUS | FANTASY
v.reasons     # sentences citing the actual numbers
```

If you tried 40 configurations and kept the best, the expected best-of-40
Sharpe under no edge is already positive. The audit computes that hurdle,
runs a Probabilistic Sharpe test against it with skew and kurtosis
included, checks whether the in-sample winner survives out-of-sample
blocks (PBO), and compares against a sign-flipped null. REAL needs DSR
probability at 0.95 or higher, permutation p under 0.05, and PBO under 0.2
where a strategy set was provided.

{GANTT}

---

## The leak, in 3D

<p align="center">
  <img src="docs/graphics/scene3d.png" alt="both equity curves as 3d ribbons over the leak plane, software rendered" width="100%">
</p>

No GPU, no renderer dependency: a numpy painter with a pinhole camera
projects the two ribbons, the leak plane sits between the lanes, and the
painter's order does the depth. The source below is the complete renderer,
the same machinery that drew the hero image and every video frame.

{THREEJS}

---

## The leak, in motion

This SVG animates right on this page: the crimson read marches toward the
future revision on a loop while the honest line holds. Pure SMIL, no
scripts, no GIF, a few kilobytes.

<p align="center">
  <img src="docs/graphics/leak_pulse.svg" alt="animated: the naive read reaches into the future" width="820">
</p>

{CSS_PULSE}

{MANIM}

{FFMPEG}

---

## Sound

Equity maps to pitch over two octaves. During the leak window the naive
voice is pinned to the honest contour with a detune ramping to 75 cents.
Two tones at f and f plus d produce amplitude modulation at rate d, so the
dishonest path beats against the honest one until the restatement date,
where it snaps free. The mp4 above carries this soundtrack.

{SONIFY}

{MIDI}

{LIBROSA}

---

## Graphs

{MATPLOTLIB}

{CHARTJS}

{XYCHART}

---

## The physical world

{OPENSCAD}

{GODOT}

### The kill switch, on real hardware

| Function | BCM | Physical pin | Wire |
|---|---|---|---|
| arm / reset button | BCM17 | 11 | internal pull-up to GND |
| latched indicator | BCM22 | 15 | 330 ohm to LED, crimson |
| armed indicator | BCM27 | 13 | 330 ohm to LED, sulfur |
| status heartbeat | BCM4 | 7 | 330 ohm to LED, blue |

{KILLSWITCH}

---

## Network and infrastructure

{WATCHRULES}

{TERRAFORM}

{OPENSSL}

{SQL}

---

## Machine learning

{CLASSIFIER}

---

## The syllabus

`docs/MILESTONES.md`: five milestones from "find one wrong thing in a CSV"
to "fit the square-root law on real crypto tapes" to "watch fifty lucky
monkeys get deflated". Each milestone is one concept interviewers ask about
and one demoable artifact.

## Limitations

- Fill simulation is bar-close plus latency drift. No queue-position
  modeling yet.
- PBO samples CSCV combinations above 512. Seeds are documented.
- EDGAR and ALFRED adapters are offline-safe skeletons: real endpoints and
  parsers, tested on fixtures, no network in tests.
- The Rust engine carries the guard, forensics, costs, synthetics, checks,
  and the demo. `live/` is Python-only so far.
- The runtime leak probe cannot see fixed-timestamp peeks; the AST lint
  catches those. Neither substitutes for the guard.
- The ONNX export is structurally validated (valid protobuf, opset 17) but
  not numerically compared against torch output yet.

## Status

v0.1.0. 162 Python tests, 94 Rust tests, both suites green in CI, zero
dependencies in both languages. Tokens and severity colors are pinned in
`docs/DESIGN.md`. The full build spec, including the planted-defect
contract, is `docs/SPEC.md`. Regenerate every graphic on this page with
`python tools/graphics.py` and `python tools/build_readme.py`.

MIT. Built to learn in public.
"""

    parts = {
        "{MINDMAP}": details("the six species of accidental cheating, as a mindmap", '''```mermaid
mindmap
  root((cheating by accident))
    the time traveler
      look-ahead bias
      signal uses a number the future had not produced
      caught by: the guard and the AST lint
    the ghost list
      survivorship bias
      backtesting only what still exists
      caught by: universe membership
    the moving number
      restatements
      whose number did you trade
      caught by: revisioned ledgers
    the lucky monkey
      overfitting
      best of forty trials is mostly luck
      caught by: deflated sharpe and PBO
    the free lunch
      flat slippage assumptions
      reality charges by size and spread
      priced by: square-root impact
    the shrinking pond
      universe drift
      a universe that only shrinks only wins
      caught by: point-in-time membership
```'''),
        "{GANTT}": details("the leak window as a gantt, real dates", '''```mermaid
gantt
    dateFormat YYYY-MM-DD
    title the leak window, real dates from the demo
    section SYM2.revenue
    first filing known (253.4)      :done, f1, 2024-02-09, 1d
    window of leaked decision reads :crit, leak, 2024-01-02, 2024-03-01
    restatement known (177.4)       :milestone, r1, 2024-03-01, 0d
```'''),
        "{XYCHART}": details("the same curves as a mermaid chart, in pure text", '''```mermaid
xychart-beta
    title "equity: honest vs naive (USD)"
    x-axis ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec", "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    y-axis "USD" 90000 --> 150000
    line "naive" [100000, 101000, 98000, 104000, 107000, 111000, 114000, 118000, 121000, 125000, 128000, 131000, 133000, 136000, 139000, 141000, 143000, 144000, 144000, 144000, 144000, 144000, 144000, 144000]
    line "honest" [100000, 101000, 100000, 102000, 103000, 105000, 106000, 108000, 109000, 111000, 112000, 114000, 115000, 117000, 118000, 119000, 120000, 121000, 121000, 122000, 122000, 122000, 122000, 122000]
```'''),
        "{THREEJS}": details("Three.js source: the 3D ribbons page", inline("gallery/media/equity_3d.html")),
        "{CSS_PULSE}": details("CSS keyframes source: the pulse page", inline("gallery/media/leak_pulse.html")),
        "{MANIM}": details("Manim source: the two clocks scene", inline("gallery/media/manim_two_clocks.py")),
        "{FFMPEG}": details("FFmpeg: mux the frames with the sonified audio", inline("gallery/audio/ffmpeg_splice.sh")),
        "{SONIFY}": details("sonification source: stdlib WAV writer", inline("gallery/audio/sonify.py")),
        "{MIDI}": details("MIDI source: a hand-rolled Standard MIDI File", inline("gallery/audio/midi_map.py")),
        "{LIBROSA}": details("librosa source: where the spectral centroid moves", inline("gallery/audio/analyze_leak.py")),
        "{MATPLOTLIB}": details("matplotlib source: the HD chart script", inline("gallery/media/plot_equity.py")),
        "{CHARTJS}": details("Chart.js source: interactive curves page", inline("gallery/media/equity_chart.html")),
        "{OPENSCAD}": details("OpenSCAD source: the printed case", inline("gallery/infra/case.scad")),
        "{GODOT}": details("Godot 4 source: the market room", inline("gallery/infra/market_room.gd")),
        "{KILLSWITCH}": details("gpiozero source: the kill-switch daemon", inline("gallery/infra/killswitch_pi.py")),
        "{WATCHRULES}": details("Wireshark filters and nginx rules", inline("gallery/infra/watch_rules.md")),
        "{TERRAFORM}": details("Terraform source: the report bucket", inline("gallery/infra/main.tf")),
        "{OPENSSL}": details("OpenSSL source: sign and verify a report", inline("gallery/infra/gen_keys.sh")),
        "{SQL}": details("PostgreSQL source: point-in-time seeds and the leak query", inline("gallery/infra/seeds.sql")),
        "{CLASSIFIER}": details("PyTorch source: the leak classifier, exported to ONNX", inline("gallery/infra/leak_classifier.py")),
    }

    t = template
    for k, v in parts.items():
        t = t.replace(k, v)

    out = os.path.join(REPO, "README.md")
    with open(out, "w") as f:
        f.write(t)
    print(f"wrote {out}: {len(t)} bytes, {t.count(chr(10))} lines")
    print("details blocks:", t.count("<details>"))
    print("mermaid blocks:", t.count("```mermaid"))
    print("images embedded:", t.count("<img "))
    print("em-dashes:", t.count(chr(0x2014)))
    print("ascii chart blocks:", t.count("####"))


if __name__ == "__main__":
    main()
