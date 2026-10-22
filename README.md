<p align="center">
  <img src="docs/diagrams/hero.svg" alt="nohindsight — is your strategy real, or did you cheat by accident?" width="100%">
</p>

[![ci](https://github.com/RXVEN-1907/nohindsight/actions/workflows/ci.yml/badge.svg)](https://github.com/RXVEN-1907/nohindsight/actions/workflows/ci.yml)
[![rust](https://github.com/RXVEN-1907/nohindsight/actions/workflows/rust.yml/badge.svg)](https://github.com/RXVEN-1907/nohindsight/actions/workflows/rust.yml)
[![license](https://img.shields.io/badge/license-MIT-705EB1)](LICENSE)
[![dependencies](https://img.shields.io/badge/dependencies-zero-0B0C10?labelColor=12141C)](Cargo.toml)
[![python](https://img.shields.io/badge/python-3.10%2B-705EB1)](pyproject.toml)
[![rust](https://img.shields.io/badge/rust-1.75%2B-705EB1)](Cargo.toml)

**A quant toolkit that answers one question: is your strategy real, or did you
cheat by accident?**

Not another backtester — there are plenty, and they all flatter you. This is
everything *around* the backtester: data that can't lie about when it was
known, police that catch dirty bars and time-traveling code, costs priced by
physics instead of habit, statistics that interrogate your luck, and a kill
switch between your strategy and the world.

---

## Watch it catch a cheat

<p align="center">
  <img src="docs/diagrams/terminal.svg" alt="python -m nohindsight demo — the naive strategy wins, then gets caught leaking 58 days into the future" width="760">
</p>

Two identical strategies run through the same engine, same broker, same fees.
The **only** difference: one reads a restated revenue number honestly, and one
does what every beginner's pandas merge does silently — today's number,
stamped onto every historical date. The naive one posts the better Sharpe.
Then the leak accountant prints the receipt.

```bash
git clone https://github.com/RXVEN-1907/nohindsight && cd nohindsight
python -m nohindsight demo          # the research API telling the story
cargo run --release -- demo         # the engine telling the same story, fast
```

---

## The two clocks

<p align="center">
  <img src="docs/diagrams/two-clocks.svg" alt="event_time vs knowledge_time, and the 13-day leak between them" width="860">
</p>

Every datum carries two times. `event_time` is when it happened in the world.
`knowledge_time` is when an honest observer could have first known it. A
backtest that reads past the second clock has leaked — it used information
the future hadn't produced yet. Here, that is a hard error, not a warning:

```python
from nohindsight import Clock, Ledger

ledger = Ledger("fundamentals")
ledger.append("SYM2.revenue", 253.4, "2023-12-31", "2024-02-09")   # as first filed
ledger.append("SYM2.revenue", 177.4, "2023-12-31", "2024-03-01")   # restated, 30% down

view = ledger.view(Clock("2024-02-15"))
view.get("SYM2.revenue")   # 253.4 — the number the world had that day
```

Read the terminal number on February 15 and the ledger refuses. There is no
way around the guard, because the view *is* the only way in — in Python, and
in Rust the guard is a type, not a convention.

## The split: research iterates in Python, the engine runs in Rust

| | Python (`nohindsight`) | Rust (`nh`) |
|---|---|---|
| The guard | `AsOfView` — leaks raise `LeakError` | `AsOfView` — leaks are `ReadError::Leak`, a type |
| The math | PSR/DSR (Bailey & López de Prado 2014), PBO via CSCV, permutation audits | line-for-line ports, pinned to the Python results to 1e-15 |
| The demo | `python -m nohindsight demo` | `cargo run --release -- demo` |
| Its own tests | 162 | 94 |
| Dependencies | stdlib only | std only, no unsafe |

Both implementations tell the same story and catch the same crimes. The
Python side is where you'll do your thinking; the Rust side is where the
correctness-critical machinery lives, compiled and clippy-clean.

## The five departments

<p align="center">
  <img src="docs/diagrams/architecture.svg" alt="nohindsight architecture: data → guard → strategy → costs → forensics, with checks above and live below" width="860">
</p>

| Department | What it does | The crime it stops |
|---|---|---|
| `data` | Append-only, revisioned, point-in-time ledgers; universes with ghosts; SEC EDGAR + ALFRED vintage adapters with real endpoints | The Moving Number, The Ghost List |
| `checks` | Bar-level police (OHLC, gaps, staleness, duplicates, crossed quotes), a split detector, an AST lint for future-reading code, a runtime leak probe | dirty data, The Time Traveler |
| `costs` | Fees, spread, borrow, and market impact by the square-root law: `impact = σ · √(Q/ADV)` | The Free Lunch |
| `forensics` | Probabilistic + Deflated Sharpe, Probability of Backtest Overfitting, seeded permutation tests — every verdict with reasons attached | The Lucky Monkey |
| `live` | Decimal-exact paper broker, deterministic fill emulator (partials, rejects, latency), a latching kill switch | The first live disaster |

<p align="center">
  <img src="docs/diagrams/leak-taxonomy.svg" alt="Field guide: six species of accidental backtest cheating" width="860">
</p>

## The gauntlet

<p align="center">
  <img src="docs/diagrams/forensics-gauntlet.svg" alt="From naive Sharpe to REAL / SUSPICIOUS / FANTASY verdict" width="860">
</p>

```python
from nohindsight.forensics import audit

v = audit(returns, n_trials=40)   # be honest about how many configs you tried
print(v.level)                    # REAL | SUSPICIOUS | FANTASY
```

A verdict is a number with reasons attached — not a vibe.

## Learn it by building it

[`docs/MILESTONES.md`](docs/MILESTONES.md) is the syllabus: five milestones,
each one README-worth of quant education and one demoable artifact — from
"find one wrong thing in a CSV of prices" to "fit the square-root law on real
crypto tapes" to "watch 50 lucky monkeys get deflated."

## Honest limitations (a library about honesty starts with itself)

- Simplified market simulation: fills at bar close plus latency drift; no
  queue-position modeling yet.
- PBO samples CSCV combinations above 512; seeds are documented.
- EDGAR/ALFRED adapters are offline-safe skeletons — real endpoints, real
  parsers, tested on fixtures; bring your own network and honor the SEC
  user-agent rule.
- The Rust engine carries the guard, forensics, costs, synthetics, and the
  demo; `live/` (paper broker, kill switch) is Python-only so far.
- The runtime leak probe can't see fixed-timestamp peeks; the AST lint
  catches those, and neither substitutes for the guard itself.

## Status

v0.1.0. **162 Python tests + 94 Rust tests, all green, zero dependencies in
both.** [`docs/DESIGN.md`](docs/DESIGN.md) pins the design language —
lavender is the truth, crimson is the leak, never decoration.
[`docs/SPEC.md`](docs/SPEC.md) holds the full build spec.

MIT. Built to learn in public.
