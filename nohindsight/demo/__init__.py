"""nohindsight.demo - the whole story in one run.

One synthetic market, five planted crimes, two strategies that differ in
exactly one line of code, and the two departments that judge them:

  1. Build synthetic prices (seed PRICE_SEED) and fundamentals (seed 7).
     Four data crimes are planted in the prices; one restatement is planted
     in the fundamentals: SYM2 filed $253.4m for Q4 2023, knowable
     2024-02-09, then re-filed $177.4m - 30% lower - knowable 2024-03-01.
  2. The data police (checks.run_all) read SYM3's bars and catch the raw
     4:1 split.
  3. Two strategies run the SAME trading logic through the SAME Replay
     engine, with the same broker seed, fees, and risk limits.  The only
     difference is how they read SYM2's revenue:

       honest - through an AsOfView bound to the replay clock: only what
                the world had published by then.
       naive  - ``ledger.value_at(key)`` with no clock: today's terminal
                number merged onto every historical decision date.  This
                is what every beginner's pandas merge does silently.

     The gate (identical for both): hold SYM2 while quarterly revenue is
     at least FLOOR ($200m), park in SYM1 otherwise.  The floor is a plain
     a-priori quality bar; it happens to sit between what SYM2 filed
     ($253m) and what it later admitted ($177m), which is exactly the
     seam the restatement opens.

     Replay's guard is structural but local: it wraps the ledger it was
     given.  A strategy holding a raw reference to ANOTHER ledger reads
     around the guard - the demo's naive strategy does that on purpose,
     once, with a comment, so the consequence is visible.
  4. forensics.audit interrogates both return streams (n_trials = 1).
  5. report.html: both equity curves (honest lavender, naive crimson),
     the findings (data crimes AND the restatement leak), both verdicts.
  6. The closing line, then the honest verdict in full.

Deterministic end to end: same seeds, same report bytes, every run.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Optional

from ..checks import (
    CheckReport,
    Finding,
    Severity,
    run_all as run_all_checks,
    summarize,
)
from ..core import AsOfKeyError, Clock, LeakError, Ledger
from ..data.synthetic import synthetic_fundamentals, synthetic_prices
from ..forensics import Verdict, audit
from ..live import (
    FillEmulator,
    FlatFee,
    MarketOrder,
    PaperBroker,
    Replay,
    RiskConfig,
    RiskGuard,
)
from ..report import render_html

__all__ = ["run"]

# -- the demo's own constants (stated, not hidden) ------------------------

GATED = "SYM2"     # the symbol whose revenue carries the restatement
PARK = "SYM1"      # where the strategy sits when the gate is closed
FLOOR = 200.0      # $200m quarterly revenue: the a-priori quality bar
WEIGHT = 0.35      # target position, as a fraction of starting cash
STARTING_CASH = 100_000.0
FEE_BPS = 1.0      # FlatFee commission on both engines
FILL_SEED = 7      # same fills for both engines (partials/rejects off)
N_DAYS = 750
FUND_SEED = 7      # the module default; pins the restatement numbers above
PRICE_SEED = 455   # chosen like any good exhibit: so the crime is visible.
                   # Under this seed SYM2 falls ~21% over the leak window while
                   # SYM1 rises ~26% - the honest strategy eats the drawdown it
                   # had no honest way to see coming; the naive one was already
                   # in the winner.  (The planted DATA crimes themselves - the
                   # split, the gap, the stale run, the duplicate - are
                   # seed-independent; this seed only picks the weather.)

_RISK = RiskConfig(max_drawdown=0.25, max_position_pct=0.5)
# (loosened from the defaults so the story is about the leak, not the
# kill switch: a 35%-weight position would need a ~70% window drop to latch.)


# -- the two strategies ----------------------------------------------------


class _RotationStrategy:
    """Shared trading logic.  Subclasses supply only the revenue read.

    The rule, identical for both: while the known quarterly revenue is at
    least FLOOR, hold GATED; otherwise hold PARK.  Sizing is a fixed
    WEIGHT of starting cash at each entry price (the demo's stated
    simplification: it does not track account drift between its two
    switches).  Decisions use the bar that just closed; orders fill at
    that same close through the broker, like Replay intends.
    """

    def __init__(self) -> None:
        self.holding: Optional[str] = None
        self.shares: int = 0
        self.n_switches = 0
        self.hold_days = 0  # days spent in the gated symbol

    def read_revenue(self) -> Optional[float]:
        """The single line the two strategies disagree on."""
        raise NotImplementedError

    def on_bar(self, view: Any, ctx: Any) -> None:
        if GATED not in ctx.bars or PARK not in ctx.bars:
            return  # a step that delivered no tradable bars
        revenue = self.read_revenue()
        gate_open = revenue is not None and revenue >= FLOOR
        target = GATED if gate_open else PARK
        if target != self.holding:
            if self.holding is not None and self.shares > 0:
                ctx.submit(MarketOrder(self.holding, -self.shares))
            close = float(ctx.bars[target]["close"])
            qty = int(WEIGHT * STARTING_CASH / close)
            if qty > 0:
                ctx.submit(MarketOrder(target, qty))
                self.holding = target
                self.shares = qty
                self.n_switches += 1
        if self.holding == GATED:
            self.hold_days += 1


class HonestStrategy(_RotationStrategy):
    """Reads revenue through an AsOfView bound to the replay clock."""

    def __init__(self, fund_ledger: Ledger, clock: Clock) -> None:
        super().__init__()
        self._view = fund_ledger.view(clock)  # the SAME clock Replay advances

    def read_revenue(self) -> Optional[float]:
        try:
            return float(self._view.get(f"{GATED}.revenue"))
        except (AsOfKeyError, LeakError):
            return None  # before the first filing, the number does not exist


class NaiveStrategy(_RotationStrategy):
    """Reads revenue from the raw ledger with no clock.

    ``value_at(key)`` with as_of=None is 'the number as the terminal shows
    it today' - the final restated value - stamped onto every historical
    decision date.  Every beginner's pandas merge does this silently; this
    class does it loudly, once, with a comment.
    """

    def __init__(self, fund_ledger: Ledger) -> None:
        super().__init__()
        self._ledger = fund_ledger

    def read_revenue(self) -> Optional[float]:
        return float(self._ledger.value_at(f"{GATED}.revenue"))  # unguarded, on purpose


# -- the leak accounting ----------------------------------------------------


def _restatement(fund_ledger: Ledger) -> tuple[Any, Any]:
    """(original, restated) entries for the planted SYM2 restatement."""
    history = fund_ledger.history(f"{GATED}.revenue")
    last = history[-1]  # the restatement is the last-appended, latest-knowing revision
    original = max(
        (e for e in history if e.knowledge_time < last.knowledge_time),
        key=lambda e: e.knowledge_time,
    )
    return original, last


def _restatement_leak_finding(fund_ledger: Ledger, price_days: list[datetime]) -> Finding:
    """One finding that itemizes the naive read's crime.

    The naive read returns the terminal revision on every decision day;
    for every day before that revision's knowledge_time, the read used
    knowledge the world did not have yet.  This is the post-hoc version of
    what a runtime guard would have raised on the spot.
    """
    _, restated = _restatement(fund_ledger)
    leak_days = [ts for ts in price_days if ts < restated.knowledge_time]
    first = leak_days[0]
    max_lag = (restated.knowledge_time - first).days
    return Finding(
        code="RESTATEMENT_LEAK",
        severity=Severity.LEAK,
        message=(
            f"naive read {GATED}.revenue on {len(leak_days)} decision days from the "
            f"terminal snapshot - a revision first published "
            f"{restated.knowledge_time.date().isoformat()}, used up to {max_lag} days "
            f"before the world had it (first: {first.date().isoformat()})"
        ),
        where=f"demo/naive:{GATED}.revenue",
        detail={
            "key": f"{GATED}.revenue",
            "leak_days": len(leak_days),
            "first_leak_day": first.date().isoformat(),
            "knowledge_time": restated.knowledge_time.date().isoformat(),
            "max_lag_days": max_lag,
        },
    )


# -- helpers ----------------------------------------------------------------


def _curve(result: Any) -> list[tuple[datetime, float]]:
    return [(ts, float(eq)) for ts, eq in result.equity_curve]


def _returns(curve: list[tuple[datetime, float]]) -> list[float]:
    return [curve[i][1] / curve[i - 1][1] - 1.0 for i in range(1, len(curve))]


def _run_engine(
    strategy: _RotationStrategy, prices: Ledger, first_day: datetime,
    clock: Optional[Clock] = None,
) -> tuple[Any, Any]:
    """One Replay run with the demo's engine settings; returns (result, broker).

    ``clock`` lets the caller share ONE clock between Replay and a strategy's
    own AsOfView (the honest strategy's fundamentals view must advance with
    the replay clock or it is frozen at the first day - a leak of the
    opposite kind: it would never learn anything new).
    """
    if clock is None:
        clock = Clock(first_day)
    broker = PaperBroker(
        STARTING_CASH,
        cost_model=FlatFee(FEE_BPS),
        fill_emulator=FillEmulator(seed=FILL_SEED, partial_rate=0.0, reject_rate=0.0),
    )
    replay = Replay(prices, clock, strategy, broker, risk=RiskGuard(_RISK))
    return replay.run(), broker


def _print_verdict_row(name: str, verdict: Verdict) -> None:
    n = verdict.numbers
    print(
        f"  {name:<8} {verdict.level:<11} sharpe {n['sharpe']:>6.2f}   "
        f"dsr_prob {n['dsr_prob']:.3f}   p {n['p_value']:.3f}"
    )


# -- the run ----------------------------------------------------------------


def run(out_dir: str = ".", *, verbose: bool = True, price_seed: int = PRICE_SEED) -> dict:
    """Run the full narrative; write ``<out_dir>/report.html``; return a
    result dict (paths, verdicts, curves, the leak finding)."""
    os.makedirs(out_dir, exist_ok=True)

    def say(text: str = "") -> None:
        if verbose:
            print(text)

    # 1. the world, with its crimes
    prices = synthetic_prices(n_days=N_DAYS, seed=price_seed)
    funds = synthetic_fundamentals(symbols=8, quarters=8, seed=FUND_SEED)
    original, restated = _restatement(funds)
    say("nohindsight demo - is your strategy real, or did you cheat by accident?")
    say()
    say(
        f"Step 1. Built a synthetic market: {N_DAYS} trading days for SYM1..SYM8 "
        f"(seed {price_seed}), and quarterly fundamentals for 2022-2023 "
        f"(seed {FUND_SEED}).  Four data crimes planted in the prices; one "
        "restatement planted in the fundamentals."
    )
    say(
        f"        The crime that matters here: {GATED} filed "
        f"${original.value:,.1f}m for its last quarter, knowable "
        f"{original.knowledge_time.date().isoformat()} - then re-filed "
        f"${restated.value:,.1f}m (30% lower), knowable "
        f"{restated.knowledge_time.date().isoformat()}."
    )

    # 2. the data police over SYM3
    bars_sym3 = [
        {"ts": e.event_time.isoformat(), **e.value}
        for e in sorted(prices.history("SYM3"), key=lambda e: e.event_time)
    ]
    sym3_report = run_all_checks(bars_sym3)
    say()
    say("Step 2. The data police read SYM3's bars:")
    for line in summarize(sym3_report).splitlines():
        say(f"        {line}")
    split_caught = any(f.code == "SPLIT_SUSPECTED" for f in sym3_report.findings)
    say(
        "        The split was caught: SYM3's raw 4:1 overnight drop on 5x volume "
        if split_caught
        else "        WARNING: the split was NOT caught this run - check the seeds"
    )

    # 3. the two strategies, one engine, one difference
    price_days = sorted({e.event_time for e in prices.entries()})
    first_day = price_days[0]
    replay_clock = Clock(first_day)  # shared: Replay advances it, the honest
    honest = HonestStrategy(funds, replay_clock)  # fundamentals view reads it
    naive = NaiveStrategy(funds)
    result_h, broker_h = _run_engine(honest, prices, first_day, clock=replay_clock)
    result_n, broker_n = _run_engine(naive, prices, first_day)

    curve_h, curve_n = _curve(result_h), _curve(result_n)
    returns_h, returns_n = _returns(curve_h), _returns(curve_n)

    leak_finding = _restatement_leak_finding(funds, price_days)
    say()
    say(
        "Step 3. Two strategies, one Replay engine, same broker seed, same fees, "
        "same risk limits.  The only difference is how they read "
        f"{GATED}'s revenue:"
    )
    say("          honest - through the AsOfView: only what the world had published.")
    say(
        "          naive  - ledger.value_at(key) with no clock: today's terminal "
        "number, merged onto every historical date.  Every beginner's pandas "
        "merge does this silently."
    )
    say(
        f"        The gate: hold {GATED} while quarterly revenue >= ${FLOOR:.0f}m, "
        f"park in {PARK} otherwise."
    )
    say(
        f"        honest held {GATED} for {honest.hold_days} days, then the "
        f"restatement landed ({restated.knowledge_time.date().isoformat()}) and it "
        f"rotated into {PARK}."
    )
    say(
        f"        naive never held {GATED}: the terminal number "
        f"(${restated.value:,.1f}m) was below the bar from day one."
    )
    say(
        f"        Leak accounting: {leak_finding.message}"
    )

    # 4. forensics on both streams
    verdict_h = audit(returns_h, n_trials=1)
    verdict_n = audit(returns_n, n_trials=1)
    say()
    say("Step 4. Forensics on both return streams (n_trials = 1, seeded):")
    _print_verdict_row("honest", verdict_h)
    _print_verdict_row("naive", verdict_n)
    say(
        "        The naive one is HIGHER.  The forensics department interrogates "
        "returns; it cannot see a read."
    )

    # 5. the report
    out_path = os.path.join(out_dir, "report.html")
    report = CheckReport()
    for finding in sym3_report.findings:
        report.add(finding)
    report.add(leak_finding)
    closing = (
        "The naive strategy made more money. It was reading a number "
        "the world didn't have yet. That is the whole problem this library "
        "exists for."
    )
    render_html(
        report,
        verdict_h,
        curve_h,
        title="nohindsight - the restatement demo",
        out_path=out_path,
        leak_equity=curve_n,
        extra_verdicts=[verdict_n],
        verdict_labels=[
            "Honest strategy - AsOfView-guarded read",
            "Naive strategy - terminal read (the leak)",
        ],
        notes=[
            closing,
            "Both strategies may pass the forensic audit: the leak was not in the "
            "returns, it was in the read.  The knowledge-time police (checks) "
            "convict what the return interrogation cannot see.",
            f"Engines: Replay, PaperBroker ${STARTING_CASH:,.0f}, FlatFee {FEE_BPS:g}bp, "
            f"FillEmulator(seed {FILL_SEED}, no partials/rejects); gate floor ${FLOOR:.0f}m; "
            f"position {WEIGHT:.0%} of starting cash.",
        ],
    )
    say()
    say(f"Step 5. Wrote {out_path}")

    # 6. the closing line, then the honest verdict
    say()
    say(closing)
    say()
    say(f"Honest verdict: {verdict_h.level}")
    for reason in verdict_h.reasons:
        say(f"  - {reason}")

    return {
        "report_path": out_path,
        "honest_verdict": verdict_h,
        "naive_verdict": verdict_n,
        "honest_curve": curve_h,
        "naive_curve": curve_n,
        "leak_finding": leak_finding,
        "split_caught": split_caught,
        "honest_refusals": result_h.refusals,
        "naive_refusals": result_n.refusals,
    }
