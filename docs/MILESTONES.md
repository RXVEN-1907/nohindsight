# the syllabus

The build order is the learning order. Each milestone is one demoable thing
and one concept you will actually get asked about in interviews.

## Milestone 0: run the demo

`python -m nohindsight demo`, or `cargo run --release -- demo`. Read the
output. The demo plants five defects in synthetic data and catches all of
them. When you can explain each catch in one sentence, you understand the
problem space better than most people who trade real money.

## Milestone 1: the checks

Pull real daily prices for one symbol, any CSV. Find one wrong thing in
them. Write one check that catches it. Commit.

Concepts: OHLC integrity, split adjustment, bad ticks.

## Milestone 2: the two clocks

Add a fundamentals ledger with filing lags. Then add FRED/ALFRED vintages
and watch a macro number get revised while the guard watches. A backtest
that uses today's GDP figure for 2019 is fiction, and you will be able to
say exactly why.

Concepts: filing lag, restatements, real-time vintages, survivorship.

## Milestone 3: real costs

Measure the square-root law on free crypto tapes: fit impact against trade
size. Replace the flat slippage constant with a curve.

Concepts: spread conventions, market impact, turnover cost.

## Milestone 4: luck, measured

Implement Deflated Sharpe from the paper. Run 50 random strategy configs
on pure noise, then compare the best naive Sharpe with its deflated value.

Concepts: multiple testing, PSR/DSR, PBO, permutation tests.

## Milestone 5: the safety net

Paper-trade the honest strategy through the fill emulator. Watch the kill
switch latch after a simulated drawdown past the limit. Fix position
sizing because of a simulated failure, before a real one.

Concepts: execution realism, risk limits, latching rules.

## After the milestones

- Real SEC data through `sources/edgar.py` (free, no key, honor the
  user-agent rule).
- A `nohindsight check` GitHub Action on your own strategy repos.
- The conformance idea: a test battery a backtester can pass, and a badge
  for passing it.
