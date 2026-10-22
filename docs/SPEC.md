# nohindsight - build spec (v0.1)

One question, five departments: **is your strategy real, or did you cheat by accident?**

## Ground rules for every module

- **Pure stdlib.** No pandas, numpy, requests, or any third-party import.
  Network fetchers use `urllib.request`; math uses `math` + `statistics`
  (which has `NormalDist().cdf` / `.inv_cfp()` - use them for Φ and Φ⁻¹).
- **Read `nohindsight/core.py` first.** It is finished and tested. Build on
  `Ledger`, `Clock`, `AsOfView`, `LeakError`, `to_utc`. Do not modify core.
- **Only write the files you own.** Never touch another module's files,
  never run `git`, never `pip install`.
- **Type hints everywhere, short docstrings, no prints inside library code**
  (prints only in `cli.py` / `demo/`).
- **Tests:** `unittest`, one file per module in `tests/test_<name>.py`,
  deterministic (seed any randomness). Run from repo root:
  `python3 -m unittest tests.test_<name> -v` - must be green before you stop.
- Dataclass values stored in ledgers are plain dicts/dataclasses; keep them
  JSON-friendly where the module persists them.

---

## Module 1 - `data` (owner: everything under `nohindsight/data/`, test file `tests/test_data.py`)

Files you own:
- `nohindsight/data/__init__.py`
- `nohindsight/data/persisted.py`
- `nohindsight/data/synthetic.py`
- `nohindsight/data/tabular.py`
- `nohindsight/data/universe.py`
- `nohindsight/data/sources/edgar.py`
- `nohindsight/data/sources/fred.py`

### `persisted.py`
- `save_ledger(ledger: Ledger, path: str) -> int` - append-safe JSONL writer:
  one JSON object per Entry: `{"key","value","event_time","knowledge_time"}`
  (ISO strings, datetimes via `.isoformat()`). Write to `path + ".tmp"` then
  `os.replace` for atomicity. Return number of entries written.
- `load_ledger(path: str, name: str = "loaded") -> Ledger` - replay the JSONL
  through `Ledger.append` (revisions reconstruct themselves from order).
  Raise `ValueError` on malformed lines (mention line number).

### `synthetic.py` - the demo universe, with crimes planted in it
Deterministic given `seed` (use `random.Random(seed)`). Return `Ledger`s.
- `synthetic_prices(n_days=750, seed=42, start="2024-01-02") -> Ledger`
  named `"prices"`. Keys are symbols (`"SYM1"`..`"SYM8"`), one entry per
  trading day (skip weekends), value =
  `{"open","high","low","close","volume"}` floats. Geometric random walk,
  per-symbol drift/vol. **Planted crimes:** SYM3 has a 4:1 split (one
  overnight close→close drop by ~4x with a volume spike, adjust prior bars'
  levels going forward like a real chart would NOT - leave history raw, the
  split detector must find it); SYM5 has a 9-day gap (exchange closure);
  SYM6 has 5 consecutive stale bars (identical close); SYM7 has one
  duplicated timestamp (same day appended twice, second is a correction).
- `synthetic_fundamentals(symbols=8, quarters=8, seed=7) -> Ledger` named
  `"fundamentals"`. Keys `"<SYM>.revenue"`, values in millions. Each quarter
  has event_time = quarter end; knowledge_time = quarter end + ~45 days
  (filing lag). **Planted crime:** SYM2's most recent quarter gets a
  restatement: a second revision with knowledge_time = original + 21 days
  and value ~30% lower. The honest view must show the original until then.
- `synthetic_macro(series="UNRATE", years=6, seed=11) -> Ledger` named
  `"macro"`. Monthly values with two vintages per month: first estimate at
  month-end + 7 days, revised estimate at +38 days (revisions ±0.2-0.6).
- `synthetic_trades(n=400, seed=3) -> list[dict]` - trade records
  `{"ts","symbol","side"("buy"/"sell"),"price","qty"}` for the costs module
  to price out; qty drawn log-normal, some large (impact-relevant).

### `tabular.py`
- `ingest_csv(path, key_col, event_col, knowledge_col, value_col=None,
  value_cols=None, name="ingested") -> Ledger` - header-row CSV; values as
  floats when possible else strings; `value_cols` builds a dict value.
  Raise `ValueError` listing the first bad row if knowledge < event.

### `universe.py`
- `@dataclass Membership: symbol, listed: datetime, delisted: datetime|None`
- `class Universe` - `add(symbol, listed, delisted=None)`,
  `members_at(as_of) -> list[str]` (listed <= as_of and (delisted is None or
  as_of < delisted)), `delisted_between(start, end) -> list[str]`,
  `survivorship_report(as_of) -> dict` with counts: `{"current", "ever_listed",
  "ghosts"}` where ghosts = listed in the past, absent as_of now. The report
  exists so a backtester can ask: "did I trade a world that only contains
  survivors?"

### `sources/edgar.py` (skeleton with real endpoints, offline-safe)
- `SEC_API = "https://data.sec.gov"`; `company_concept_url(cik, taxonomy,
  tag, unit)` builds e.g. `/api/xbrl/companyconcept/CIK0000320193/us-gaap/
  Revenues/USD.json` (zero-pad CIK to 10). `fetch_json(url, user_agent)`
  via urllib with **mandatory** UA header (`"nohindsight/0.1
  (github.com/RXVEN-1907/nohindsight)"` - SEC 403s requests without one;
  document this). On `URLError`/HTTP errors raise a local
  `EdgarUnavailable` with a helpful message. 
- `facts_to_ledger(payload, tag) -> Ledger` - parse a company-concept
  payload: each fact has `end`, `filed`, `val`, `fy`, `fp`, `frame`?
  Map: key = `f"{cik}.{tag}"`, event_time = `end`, knowledge_time =
  `filed`, value = val. This is REAL point-in-time fundamentals: multiple
  entries per period = restatements, and the Ledger models them natively.
  Pure function (no network) - test it on an inline fixture payload.

### `sources/fred.py` (skeleton with real endpoints, offline-safe)
- `FRED_API = "https://api.stlouisfed.org"`; `alfred_vintage_url(series_id,
  date, api_key=None)` →
  `https://api.stlouisfed.org/fred/release/table?...` is messy - instead use
  the plain ALFRED CSV endpoint:
  `https://alfred.stlouisfed.org/graph/alfred_graph.csv?id=<SERIES>&cosd=<date>`
  (documented, no key needed) and `fred_series_url(series_id, api_key)`.
- `vintages_to_ledger(csv_text, series_id) -> Ledger` - ALFRED CSV has a
  `realtime_start` column: for each row, knowledge_time =
  realtime_start (when that revision was published), event_time =
  observation date. Pure function on CSV text; test with an inline fixture.

**Tests (>= 12):** persistence round-trip incl. revisions; malformed-line
error; synthetic prices contain the planted split/gap/stale/duplicate (find
them by scanning entries); fundamentals restatement visible in `history` but
NOT in `value_at` before its knowledge_time; CSV ingest round-trip + bad-row
error; universe survivorship counts; edgar `facts_to_ledger` on fixture
(2 filings same period → 2 revisions); fred `vintages_to_ledger` on fixture;
URL builders produce exactly-documented strings.

---

## Module 2 - `checks` (owner: everything under `nohindsight/checks/`, test file `tests/test_checks.py`)

Files you own: `nohindsight/checks/__init__.py`, `nohindsight/checks/base.py`,
`nohindsight/checks/quality.py`, `nohindsight/checks/splits.py`,
`nohindsight/checks/lookahead.py`, `nohindsight/checks/registry.py`.

### `base.py`
- `Severity` enum: `LEAK` (this invalidates the backtest), `WARN`, `INFO`.
- `@dataclass Finding: code, severity, message, where (str), detail (dict)`.
- `class CheckReport` - `.findings`, `.add(f)`, `.counts()` dict by
  severity, `.has_leaks` property, `.__bool__` = has_leaks.

### `quality.py` - bar-level police. Bars are dicts
`{"open","high","low","close","volume","ts"}` (ts ISO string), list of them
sorted ascending; every check returns `list[Finding]` and is a pure function:
- `check_ohlc(bars)` - high >= max(open,close), low <= min(open,close),
  low > 0, all finite. Violation → LEAK (data this broken poisons anything).
- `check_duplicates(bars)` - repeated ts → LEAK.
- `check_gaps(bars, expected_days=1, max_gap_days=5)` - calendar gaps
  (weekends excluded via `weekday() >= 5`) longer than max → WARN.
- `check_stale(bars, run=4)` - `run` consecutive identical closes → WARN
  (dead feed or halted symbol).
- `check_crossed(bars)` - if `"bid"`/`"ask"` present: bid > ask → LEAK.
- `check_scale_shock(bars, z=8.0)` - a single-bar move > z sigmas of the
  recent daily-move distribution → WARN (candidate split or bad tick; the
  split detector decides).

### `splits.py`
- `detect_splits(bars, vol_mult=3.0) -> list[Finding]` - overnight close-to-
  close |log return| within tolerance of ln(k) for k in {2,3,4,5,10} AND
  volume ratio > vol_mult → LEAK-severity finding with `detail={"ratio":
  k}` - because an unadjusted split in a backtest produces phantom PnL.

### `lookahead.py` - static + runtime police
- `scan_source(source: str) -> list[Finding]` - AST-based lint (module
  `ast`), rules (all WARN, message explains the suspicion):
  R1 `.shift(-` - a negative shift reads the future;
  R2 `.iloc[-` or `.loc[<negative>` - reading from the end of history;
  R3 names containing `future|tomorrow|next_day|t_plus|forward` used as
  subscripts/slices;
  R4 assignment targets compared across time in one expression is too
  fuzzy - skip. Keep rules honest: each message says "hint, not proof".
- `class LeakTrap` - runtime guard for the demo: wraps a function
  `fn(view, *a, **kw)`; after it runs, compare a second invocation with a
  clock advanced N steps ahead - if results differ in a way implying
  knowledge of the extra steps, emit LEAK finding. Simpler honest
  behavior: record `view.as_of` at call and verify every value the function
  requested came from the view (monkeypatching is overkill; instead
  provide `assert_no_future_access(fn, ledger, clock, steps=1)`:
  run fn under clock, then under clock+1 step, assert outputs identical -
  a strategy that peeks at data beyond its clock is deterministic, so
  differing outputs expose it). Document the assumption.

### `registry.py`
- `run_all(bars, *, with_lookahead_scan: str|None = None) -> CheckReport`
  - run every quality check + splits; if `with_lookahead_scan` source given,
  add `scan_source` findings. Provide `summarize(report) -> str` - a short
  aligned text table (code, severity, count) for the CLI.

**Tests (>= 14):** each check with a crafted-fixture (clean bars → no
findings; one broken bar → exactly one finding of right code/severity);
split detector fires on planted SYM3 pattern and not on noise; stale run
threshold; duplicate; AST rules on small code strings (shift(-1) caught,
shift(1) not); `assert_no_future_access` passes an honest strategy and
fails a peeking one; registry aggregates.

---

## Module 3 - `costs` (owner: everything under `nohindsight/costs/`, test file `tests/test_costs.py`)

Files you own: `nohindsight/costs/__init__.py`, `nohindsight/costs/model.py`.
(One file is fine; split only if it grows past ~400 lines.)

### `model.py`
- `@dataclass Trade: ts (str|datetime), symbol, side ("buy"/"sell"), price,
  qty, sigma_bps (float|None=None), adv (float|None=None), holding_days
  (float=0.0)`.
- `@dataclass CostBreakdown: fee, spread_cost, impact_cost, borrow_cost,
  total, total_bps` (monetary units; total_bps = total / notional * 1e4).
- `class FeeSchedule` - `flat_bps(bps)` classmethod, `per_share(cents)`,
  `zero()`; `.apply(notional, shares) -> float`.
- `class SpreadModel` - `constant(bps)`, `volatility_scaled(base_bps, mult=8.0)`
  (spread_bps = base_bps * (1 + mult * sigma_bps/1000) when sigma known
  else base_bps); `.half_cost(price, qty) -> float` - you cross the spread
  on entry and exit: cost = half_spread * 2 sides? NO: half-spread per
  crossing, and each trade crosses once → cost = price*qty*half_spread/1e4.
  Document the convention loudly.
- `class ImpactModel` - **square-root law** (cite: Bouchaud et al.,
  Almgren-Chriss 2000): `impact_bps = coeff * sigma_bps * sqrt(qty/adv)`
  with `coeff=1.0` default; `linear(qty/adv)` alternative
  `eta * participation`, `eta=0.1`. `None` sigma/adv → impact 0 (and an
  `explained` flag so reports can say "impact unestimated - no sigma/ADV").
- `class BorrowModel` - `annual(rate=0.05)`; short positions pay
  `notional * rate * holding_days/365`.
- `class CostModel(fees, spread, impact=None, borrow=None)` -
  `.price(trade) -> CostBreakdown`, `.apply(trades) -> list[CostBreakdown]`,
  `.total_bps(trades) -> float`.
- `def fantasy_vs_real(returns_no_cost: list[float], costs_bps: float,
  periods_per_year: int) -> dict` - annualized Sharpe before costs, after
  applying per-period cost drag (`costs_bps/1e4` per period subtracted from
  mean return - document this simplification), and the shrinkage ratio.
  This is the function that turns "0.1% per trade is nothing" into a number.

**Tests (>= 10):** fee schedules; half-spread convention; sqrt impact
scaling (quadruple qty → 2x impact); borrow proration; composition sums;
total_bps sign/scale sanity; fantasy_vs_real monotone in costs and in
turnover; zero-cost identity.

---

## Module 4 - `forensics` (owner: everything under `nohindsight/forensics/`, test file `tests/test_forensics.py`)

Files you own: `nohindsight/forensics/__init__.py`,
`nohindsight/forensics/stats.py`, `nohindsight/forensics/deflate.py`,
`nohindsight/forensics/pbo.py`, `nohindsight/forensics/audit.py`.

### `stats.py`
- `sharpe(returns, periods_per_year=252) -> float` (sample std, ddof=1,
  annualized ×√ppy), `sortino`, `max_drawdown(equity_or_returns) -> float`
  (positive number, worst peak-to-trough), `calmar`, `t_stat(returns)`
  (mean/(std/√n)).

### `deflate.py` - Bailey & López de Prado (2014), "The Deflated Sharpe
Ratio" (SSRN 2460551). Use `statistics.NormalDist` for Φ/Φ⁻¹. All
functions take per-period returns.
- `moments(returns) -> (mean, std, skew, kurtosis)` - Pearson kurtosis
  (normal = 3.0), population moments on the sample, document it.
- `psr(returns, benchmark_sr, periods_per_year=252) -> float` -
  PSR = Φ( (SR − SR*)·√(n−1) / √(1 − γ3·SR + ((γ4−1)/4)·SR²) ) with SR
  annualized, γ3 skew, γ4 kurtosis; clamp denominator to >= 1e-12; SR
  must be the ANNUALIZED Sharpe and the (1 − γ3·SR + …) term uses the
  annualized SR per the paper's convention - implement per the paper,
  state the convention in the docstring, and include a worked numeric
  test matching a hand-computed value.
- `expected_max_sharpe(trial_sharpes: list[float]) -> float` -
  SR0 = √(V[SR trials]) · ( (1−γ)·Z(1−1/N) + γ·Z(1−1/(N·e)) ),
  γ = 0.5772156649015329 (Euler–Mascheroni), Z = NormalDist().inv_cdf,
  e = math.e. N = number of trials.
- `deflated_psr(returns, n_trials, trial_sharpes=None, ppy=252) -> float`
  - PSR against SR0 from trials (or from variance 1.0 default when trial
  Sharpes unavailable - document that fallback as conservative-ish).

### `pbo.py` - probability of backtest overfitting, CSCV
(Bailey, Borwein, López de Prado, Zhu 2017).
- `pbo(strategy_returns: list[list[float]], blocks=16, max_combos=512,
  seed=0) -> float` - rows = time, columns = configs. Split rows into
  `blocks` even blocks; enumerate all C(blocks, blocks/2) train
  combinations (sample `max_combos` seeded via `random.Random(seed)` if
  larger); for each: pick IS-best config by Sharpe, find its rank among
  OOS Sharpes, logit λ = ln(rel/(1−rel)) with rel=(rank)/(n_configs−1)
  mapped from rank position (rank 0 best → λ → −∞; define precisely and
  test); **PBO = fraction of combinations where the IS-best config ranks
  in the worse half OOS** (λ ≤ 0). If C is even the border rank maps to
  λ=0 - count it as overfit (conservative), document.
- `overfit_report(...)` - dict with pbo, n_combos, is_best_oos_median_rank.

### `audit.py`
- `@dataclass Verdict: level ("REAL"|"SUSPICIOUS"|"FANTASY"), numbers (dict
  of everything computed), reasons (list[str] - plain-language sentences,
  one per test, e.g. "Sharpe 1.8 but you ran 40 trials; the best of 40
  luckies is ~1.1, and yours barely clears it")`.
- `audit(returns, n_trials=1, strategy_returns=None, blocks=16, ppy=252,
  mc_paths=2000, seed=0) -> Verdict` - compute: Sharpe, DSR probability,
  PBO (if strategy_returns given), and a seeded Monte-Carlo permutation
  p-value (flip each return's sign via `random.Random(seed)`, mc_paths
  draws, p = fraction of permuted Sharpes >= observed - a two-sided-free
  one-sided bootstrap of the null "no edge"). Thresholds (documented):
  REAL: DSR prob >= 0.95 and p < 0.05 and (PBO if present) < 0.2;
  FANTASY: DSR prob < 0.5 or p >= 0.2 or PBO >= 0.5; else SUSPICIOUS.
  Reasons must cite the actual numbers.

**Tests (>= 14):** sharpe/dd on hand-built series; psr matches a
hand-computed worked example (compute the expected number in the test
with independent arithmetic in the test file); psr monotone in n and in
skew penalty; expected_max_sharpe grows with N and with trial variance;
pbo ≈ 0 for one clearly-best config, ≈ high for all-noise configs
(with seed, assert ranges not exact noise); permutation p-value ≈ 1 for
symmetric noise, < 0.05 for strong drift; verdict levels across crafted
inputs; reasons mention numbers.

---

## Module 5 - `live` (owner: everything under `nohindsight/live/`, test file `tests/test_live.py`)

Files you own: `nohindsight/live/__init__.py`, `nohindsight/live/orders.py`,
`nohindsight/live/fillsim.py`, `nohindsight/live/broker.py`,
`nohindsight/live/risk.py`, `nohindsight/live/replay.py`.

### `orders.py`
- `@dataclass MarketOrder: symbol, qty (sign = direction)`;
  `@dataclass LimitOrder: symbol, qty, limit_price`;
  `@dataclass Fill: order_id, symbol, qty, price, ts, fee` ;
  `@dataclass Refusal: order_id, rule, message`.

### `fillsim.py` - deterministic cruelty for your algo
- `class FillEmulator(seed=0, partial_rate=0.15, reject_rate=0.02,
  latency_ms=(5, 250))` - seeded `random.Random`. `fill(order, bar) ->
  list[Fill]` where bar is a price dict: market orders fill at bar close ±
  latency drift (documented simplification), sliced into 1-3 partial fills
  with `partial_rate` probability (sum = qty exactly - partial fills must
  never lose size), `reject_rate` of orders raise nothing but return empty
  + a Refusal via the broker (return `(fills, refusal|None)`). Limit
  orders fill only if bar range crosses the limit (low <= limit <= high
  for buys). All deterministic given seed.

### `broker.py` - decimal-exact paper money
- `class PaperBroker(starting_cash, cost_model=None)` - **`decimal.Decimal`**
  for cash/positions/PnL (float drift is a crime in a repo about honesty;
  prices/qty convert via `Decimal(str(x))`). Methods: `submit(order)`,
  `_execute(order, bar, ts)` (uses FillEmulator + optional CostModel for
  fees), `position(symbol) -> Decimal`, `cash`, `equity(prices: dict)`,
  `trades -> list[Fill]`, `equity_curve -> list[(ts, Decimal)]`.
  Shorting allowed (negative positions). Buying power check: cash >= 0
  after trades (margin = refuse).

### `risk.py` - the kill switch
- `@dataclass RiskConfig: max_drawdown=0.15, max_position_pct=0.25,
  fat_finger_price_mult=5.0, fat_finger_qty_mult=20.0,
  max_orders_per_bar=10, kill_switch_latch=True`.
- `class RiskGuard(config)` - `.approve(order, account_state, last_price) ->
  Refusal|None`, called by Replay between strategy and broker. Rules:
  drawdown from high-water mark > max_drawdown → REFUSE and (latch) refuse
  everything forever (a blown-up account doesn't get to keep trading);
  position value after order > pct of equity → refuse; order price/qty
  vs last_price and typical size → fat-finger refuse; order rate per bar.
  `.latched` property.

### `replay.py` - the honest backtest loop
- `class Replay(ledger, clock, strategy, broker, risk, on_event=None)`.
  `run()` iterates event times: advance clock to bar ts, get the bar dict
  per symbol **only via `AsOfView`**, call `strategy.on_bar(view, ctx)`
  where `ctx.submit(order)` goes through `risk.approve` then
  `broker._execute`; record every Refusal; append equity to curve. If the
  strategy touches the raw ledger (it can't - it only gets the view) - the
  guard is structural; note that in the docstring.
- `class Ctx` - minimal: `.submit(order)`, `.now`, `.refusals`.

**Tests (>= 12):** fill emulator determinism (same seed → same fills);
partials sum exactly; rejects produce refusal not exception; limit fills
only in-range; broker cash math exact to the cent on a scripted scenario
(e.g., buy 10 @ 100 + fee, assert Decimal equality); short positions;
equity = cash + Σ position×price; risk latch: after synthetic 20% drawdown
every order refused; fat-finger; order-rate cap; replay runs an honest
strategy end-to-end over synthetic_prices with zero LeakErrors and produces
a monotone-time equity curve.

---

## Module 6 - integration (second phase; owner: `nohindsight/cli.py`,
`nohindsight/report.py`, `nohindsight/demo/*`, `tests/test_cli.py`)

Read every module first. Then:
### `report.py`
- `render_html(report: CheckReport|None, verdict: Verdict|None, equity:
  list[(ts, float)]|None, title, out_path)` - a single self-contained HTML
  file (inline CSS, **no external fonts/scripts**), dark theme:
  bg `#0B0C10`, text `#F2F3F7`, truth/baseline lavender `#D0C4DF`,
  findings crimson `#E5484D`, warn sulfur `#E6B455`, info blue `#5B8DEF`.
  Sections: verdict card (level + reasons), findings table, inline SVG
  equity sparkline (hand-built path, lavender line on dark). Clean
  typography: system font stack, generous spacing, `max-width: 860px`.

### `cli.py` - `python -m nohindsight <cmd>` via argparse
- `version` - print version.
- `check <csv>` - ingest via data.tabular, run checks.registry, print
  summary + write HTML report next to it (`--out`).
- `audit <returns_csv>` - one column of returns (or `--demo` for synthetic);
  run forensics.audit; print verdict banner + reasons; write report.
- `demo [--out DIR]` - the full story (see demo below); prints a
  step-by-step narrative to stdout (plain words), writes `report.html`.

### `demo/` - `python -m nohindsight demo`
`nohindsight/demo/__init__.py` + logic in `nohindsight/demo/__init__.py`
itself (no runner scripts needed). Narrative:
1. Build synthetic prices + fundamentals (crimes planted).
2. `checks.run_all` on SYM3 bars → show the split was caught.
3. Two strategies over the same data: **honest** (uses AsOfView) vs
   **naive** (reads `value_at(key, revised.knowledge_time)` - today's
   restated number - for past decisions, i.e., classic restatement leak).
   Run honest through Replay (guarded); run naive with a deliberately
   unguarded read (document that this is what every beginner's pandas
   merge does silently). Naive must look BETTER (higher Sharpe).
4. `forensics.audit` both return streams (n_trials=1 honest, naive=1):
   print the two verdicts side by side.
5. Write `report.html` with both equity curves (honest lavender, naive
   crimson - the leak line) + findings + verdicts.
6. Print: "The naive strategy made more money. It was reading a number
   the world didn't have yet. That is the whole problem this library
   exists for."  - then the honest verdict.

`tests/test_cli.py` (>= 4): `main(["version"])` prints version; `demo`
completes in a temp dir, writes report.html containing "nohindsight" and
a verdict string; audit on a drift-y synthetic CSV returns REAL-ish;
check on a broken CSV exits nonzero flag (or reports LEAK count > 0 via
return value - design: `main(argv) -> int`).
