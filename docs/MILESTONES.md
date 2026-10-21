# nohindsight — the syllabus

The build order is the learning order. Each milestone is one README-worth of
quant education and one demoable thing to show. You do not need to know what
it all is when you start — that is what the milestones are for.

## Milestone 0 — run the demo
`python -m nohindsight demo`. Read every line of output. The demo plants
five crimes in synthetic data and catches them. When you can explain each
catch in one sentence, you understand the problem space better than most
people who trade real money.

## Milestone 1 — the police (`checks/`)
Pull real daily prices for one symbol (any CSV). Find one wrong thing in
them. Write one check that catches it. Commit. You now know why data
cleaning is a quant's first job, not a chore.

**Learn:** OHLC integrity, split adjustment, what a "bad tick" is.

## Milestone 2 — the two clocks (`data/`)
Add a fundamentals ledger with filing lags. Then add FRED/ALFRED vintages
and watch a macro number get revised under your guard. Understand why a
backtest that uses today's GDP for 2019 is fiction.

**Learn:** filing lag, restatements, real-time vintages, survivorship.

## Milestone 3 — reality tax (`costs/`)
Measure the square-root law on real (free) crypto tapes: fit impact vs
trade size. Replace the flat slippage constant in your head with a curve.

**Learn:** spread conventions, market impact, why turnover is expensive.

## Milestone 4 — is it luck? (`forensics/`)
Implement Deflated Sharpe by hand from the paper. Try 50 random strategy
configs on noise; watch the best one post a great naive Sharpe and a
humiliating DSR. That one experiment is worth a semester.

**Learn:** multiple testing, PSR/DSR, PBO, permutation tests.

## Milestone 5 — the safety net (`live/`)
Paper-trade the honest strategy through the fill emulator. Watch the kill
switch latch after a simulated 16% drawdown. Fix your position sizing
because of a *simulated* disaster, before a real one teaches you.

**Learn:** execution realism, risk limits, why drawdown rules must latch.

## After the milestones

- Real SEC data through `sources/edgar.py` (free, no key, honor the UA
  header) — a real point-in-time fundamentals dataset.
- A `nohindsight check` GitHub Action on your own strategy repos.
- The conformance idea: publish a badge other backtesters can earn.
