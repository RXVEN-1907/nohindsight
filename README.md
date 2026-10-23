<p align="center">
  <img src="docs/diagrams/hero.svg" alt="nohindsight: the two clocks, the leak window, the verdicts" width="100%">
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
  <img src="docs/graphics/leak.gif" alt="the honest and naive equity curves diverge inside the leak window" width="820">
</p>

| | verdict | sharpe | permutation p |
|---|---|---|---|
| honest (guarded read) | SUSPICIOUS | 0.69 | 0.111 |
| naive (leaking read) | REAL | 1.17 | 0.020 |

Leak accounting: 43 decision days priced off a revision first published
2024-03-01, first read 2024-01-02, up to 58 days before the world had it.

<p align="center">
  <img src="docs/diagrams/receipt.svg" alt="the evidence board" width="720">
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

<p align="center">
  <img src="docs/diagrams/leak_pulse.svg" alt="animated: the naive read reaches toward a revision the world does not have yet" width="800">
</p>

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
    D["data: point-in-time ledgers"] --> G{{"guard: AsOfView"}}
    G --> S["strategy on a Clock"]
    S --> C["costs: fees, spread, impact"]
    C --> F["forensics: DSR, PBO, permutation"]
    K["checks: scans data and code"] -. findings .-> D
    K -. findings .-> S
    F --> L["live: paper broker, kill switch"]
    L -. same code path .-> D
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

<p align="center">
  <img src="docs/graphics/deflation.png" alt="the hurdle your sharpe must clear, rising with the number of trials" width="820">
</p>
<p align="center">
  <sub>the hurdle rises with every configuration you try. at the crimson dot, 1.17 stops meaning anything.</sub>
</p>

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

v0.1.0. 162 Python tests, 94 Rust tests, zero dependencies in both languages.
Build spec: `docs/SPEC.md`. Design tokens: `docs/DESIGN.md`.

MIT. Built to learn in public.
