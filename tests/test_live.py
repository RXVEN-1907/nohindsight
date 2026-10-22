"""tests.test_live - the live department on trial.

Assumptions baked into these tests (stated, not hidden):

* Bar ledgers used here are LOCAL fixtures - the data module is being built
  in parallel, so nothing imports nohindsight.data.  Fixture ledgers encode
  the contract Replay relies on: a bar's knowledge_time equals its
  event_time (the close is knowable at the close).
* Exactness scenarios use FillEmulator(latency_ms=(0, 0), partial_rate=0,
  reject_rate=0) - zero latency means zero slip, so a market order fills at
  the close EXACTLY and Decimal equality is meaningful.
* Every random source is seeded; the suite is fully deterministic.
"""

from __future__ import annotations

import random
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from nohindsight.core import AsOfKeyError, Clock, Ledger, LeakError, to_utc
from nohindsight.live import (
    AccountState,
    Ctx,
    FillEmulator,
    FlatFee,
    LimitOrder,
    MarketOrder,
    PaperBroker,
    Replay,
    RiskConfig,
    RiskGuard,
    to_decimal,
)

UTC = timezone.utc


# -- fixtures -----------------------------------------------------------------


def make_bar(ts=None, close=100.0, low=None, high=None, open_=None, volume=1000.0):
    """A bar dict; carries 'ts' only when a ts is given (ledger bars do not)."""
    low = close * 0.98 if low is None else low
    high = close * 1.02 if high is None else high
    bar = {
        "open": close if open_ is None else open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }
    if ts is not None:
        bar["ts"] = ts
    return bar


def trading_days(start: datetime, n: int) -> list[datetime]:
    """n weekday timestamps starting at `start`, one per trading day."""
    days: list[datetime] = []
    day = start
    while len(days) < n:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def price_ledger(symbols=("SYM1", "SYM2"), n_days=40, seed=5) -> Ledger:
    """A local fixture bar ledger: keys are symbols, values are bar dicts,
    knowledge_time == event_time (the close is knowable at the close)."""
    rng = random.Random(seed)
    led = Ledger("prices")
    days = trading_days(datetime(2024, 1, 1, 21, tzinfo=UTC), n_days)
    levels = {s: 100.0 + 7.0 * i for i, s in enumerate(symbols)}
    for day in days:
        for s in symbols:
            levels[s] = max(1.0, levels[s] * (1.0 + rng.gauss(0.0, 1.5) / 200.0))
            close = round(levels[s], 2)
            bar = {
                "open": round(close * 0.999, 2),
                "high": round(close * 1.01, 2),
                "low": round(close * 0.99, 2),
                "close": close,
                "volume": float(rng.randint(1000, 5000)),
            }
            led.append(s, bar, day, day)
    return led


def acct(equity, cash=None, positions=None) -> AccountState:
    eq = to_decimal(equity)
    return AccountState(
        cash=to_decimal(cash if cash is not None else equity),
        equity=eq,
        positions=positions or {},
    )


def exact_emulator(seed: int = 1) -> FillEmulator:
    """No latency slip, no partials, no rejects: fills land exactly on the close."""
    return FillEmulator(seed=seed, partial_rate=0.0, reject_rate=0.0, latency_ms=(0, 0))


BAR_TS = "2024-01-02T21:00:00+00:00"


def to_utc_ts(iso: str) -> datetime:
    return to_utc(iso)


# -- orders ---------------------------------------------------------------------


class TestOrders(unittest.TestCase):
    def test_order_ids_unique(self):
        ids = [MarketOrder("S", 1).order_id for _ in range(100)]
        self.assertEqual(len(set(ids)), 100)
        for order in (LimitOrder("S", 1, 10.0), MarketOrder("S", 1)):
            self.assertTrue(order.order_id.startswith("ord-"))


# -- fillsim --------------------------------------------------------------------


class TestFillEmulator(unittest.TestCase):
    def setUp(self):
        self.bar = make_bar(ts=BAR_TS, close=100.0)

    def test_market_fill_determinism(self):
        """Same seed -> same fills, byte for byte, including timestamps."""
        e1 = FillEmulator(seed=7, reject_rate=0.0)
        e2 = FillEmulator(seed=7, reject_rate=0.0)
        order = MarketOrder("SYM1", 37)
        f1, r1 = e1.fill(order, self.bar)
        f2, r2 = e2.fill(order, self.bar)
        self.assertIsNone(r1)
        self.assertIsNone(r2)
        self.assertEqual(f1, f2)
        self.assertEqual(sum(f.qty for f in f1), Decimal("37"))
        self.assertEqual(len(f1), 1)  # default partial_rate is 0.15; seed 7 says single fill

    def test_partials_sum_exactly_over_stream(self):
        """Partial fills must never lose size - for ints, floats and Decimals,
        buys and sells, over a long seeded stream."""
        e = FillEmulator(seed=11, partial_rate=0.35, reject_rate=0.0)
        qtys = [
            Decimal("123.45"), Decimal("1000"), Decimal("0.125"),
            7, 99.5, Decimal("3.14159"), -7, Decimal("-12.5"),
        ]
        for i in range(40):
            q = qtys[i % len(qtys)]
            fills, refusal = e.fill(MarketOrder("S", q), self.bar)
            self.assertIsNone(refusal)
            self.assertEqual(sum((f.qty for f in fills), Decimal(0)), to_decimal(q))
            sign = -1 if to_decimal(q) < 0 else 1
            for f in fills:
                self.assertEqual(f.qty, sign * abs(f.qty))  # no mixed-sign slices
                self.assertGreater(abs(f.qty), 0)

    def test_partials_forced_and_multi(self):
        """partial_rate=1.0 slices into 2-3 fills and still closes on the qty."""
        e = FillEmulator(seed=13, partial_rate=1.0, reject_rate=0.0)
        multi = 0
        for q in (Decimal("100"), 37, Decimal("0.5"), 1234.5):
            for _ in range(10):
                fills, refusal = e.fill(MarketOrder("S", q), self.bar)
                self.assertIsNone(refusal)
                self.assertEqual(sum((f.qty for f in fills), Decimal(0)), to_decimal(q))
                self.assertLessEqual(len(fills), 3)
                if len(fills) > 1:
                    multi += 1
        self.assertGreater(multi, 0)

    def test_reject_yields_refusal_not_exception(self):
        e = FillEmulator(seed=3, reject_rate=1.0)
        for _ in range(5):
            fills, refusal = e.fill(MarketOrder("S", 10), self.bar)
            self.assertEqual(fills, [])
            self.assertIsNotNone(refusal)
            self.assertEqual(refusal.rule, "rejected")
            self.assertTrue(refusal.message)

    def test_limit_buy_fills_in_range_at_limit(self):
        e = FillEmulator(seed=5, reject_rate=0.0)
        bar = make_bar(ts=BAR_TS, close=100.0, low=98.0, high=102.0)
        fills, refusal = e.fill(LimitOrder("S", 10, 100.5), bar)
        self.assertIsNone(refusal)
        self.assertGreater(len(fills), 0)
        for f in fills:
            self.assertEqual(f.price, Decimal("100.5"))
        self.assertEqual(sum((f.qty for f in fills), Decimal(0)), Decimal("10"))

    def test_limit_out_of_range_is_silently_unfilled(self):
        """No fill, and NOT a refusal - the market never touched the price."""
        e = FillEmulator(seed=5, reject_rate=0.0)
        bar = make_bar(ts=BAR_TS, close=100.0, low=98.0, high=102.0)
        fills, refusal = e.fill(LimitOrder("S", 10, 103.0), bar)  # buy above high
        self.assertEqual(fills, [])
        self.assertIsNone(refusal)
        fills, refusal = e.fill(LimitOrder("S", -10, 97.0), bar)  # sell below low
        self.assertEqual(fills, [])
        self.assertIsNone(refusal)

    def test_limit_sell_fills_in_range_at_limit(self):
        e = FillEmulator(seed=5, reject_rate=0.0)
        bar = make_bar(ts=BAR_TS, close=100.0, low=98.0, high=102.0)
        fills, refusal = e.fill(LimitOrder("S", -10, 99.0), bar)
        self.assertIsNone(refusal)
        self.assertGreater(len(fills), 0)
        for f in fills:
            self.assertEqual(f.price, Decimal("99"))
            self.assertEqual(f.qty, -abs(f.qty))

    def test_latency_moves_ts_and_drifts_price_adversely(self):
        e = FillEmulator(seed=9, reject_rate=0.0, latency_ms=(1000, 2000))
        ts = datetime(2024, 1, 2, 21, tzinfo=UTC)
        bar = make_bar(close=100.0)  # no 'ts' key: the explicit ts must be used
        fills, refusal = e.fill(MarketOrder("S", 10), bar, ts=ts)
        self.assertIsNone(refusal)
        for f in fills:
            self.assertIsNotNone(f.ts.tzinfo)
            self.assertGreaterEqual(f.ts - ts, timedelta(seconds=1))
            self.assertLessEqual(f.ts - ts, timedelta(seconds=2))
            self.assertGreaterEqual(f.price, Decimal("100"))  # buys never fill below close
        # The bar["ts"] fallback path stamps from the bar itself.
        bar_with_ts = make_bar(ts=BAR_TS, close=100.0)
        fills2, _ = e.fill(MarketOrder("S", 10), bar_with_ts)
        self.assertGreater(fills2[0].ts, to_utc_ts(BAR_TS))


# -- broker ---------------------------------------------------------------------


class TestPaperBroker(unittest.TestCase):
    def test_cash_math_exact_to_the_cent(self):
        """Buy 10 @ 100 with a 25 bps fee, then sell 4 @ 110 - Decimal equality,
        not isclose."""
        broker = PaperBroker(Decimal("10000"), cost_model=FlatFee(25), fill_emulator=exact_emulator())
        broker.on_bar("SYM1", make_bar(ts="2024-01-02T21:00:00+00:00", close=100.0))
        fills, refusal = broker.submit(MarketOrder("SYM1", 10))
        self.assertIsNone(refusal)
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0].price, Decimal("100"))
        self.assertEqual(fills[0].fee, Decimal("2.5"))  # 1000 notional * 25 bps
        self.assertEqual(broker.cash, Decimal("10000") - Decimal("1000") - Decimal("2.5"))
        self.assertEqual(broker.position("SYM1"), Decimal("10"))

        broker.on_bar("SYM1", make_bar(ts="2024-01-03T21:00:00+00:00", close=110.0))
        fills, refusal = broker.submit(MarketOrder("SYM1", -4))
        self.assertIsNone(refusal)
        self.assertEqual(fills[0].fee, Decimal("1.1"))  # 440 notional * 25 bps
        self.assertEqual(broker.cash, Decimal("9436.4"))
        self.assertEqual(broker.position("SYM1"), Decimal("6"))

    def test_short_position_round_trip(self):
        """Shorting credits cash; buying back flattens; no margin needed to open."""
        broker = PaperBroker(Decimal("10000"), fill_emulator=exact_emulator())
        broker.on_bar("SYM1", make_bar(ts=BAR_TS, close=50.0))
        fills, refusal = broker.submit(MarketOrder("SYM1", -5))
        self.assertIsNone(refusal)
        self.assertEqual(broker.position("SYM1"), Decimal("-5"))
        self.assertEqual(broker.cash, Decimal("10250"))
        broker.on_bar("SYM1", make_bar(ts="2024-01-03T21:00:00+00:00", close=40.0))
        fills, refusal = broker.submit(MarketOrder("SYM1", 5))
        self.assertIsNone(refusal)
        self.assertEqual(broker.position("SYM1"), Decimal("0"))
        self.assertEqual(broker.cash, Decimal("10050"))

    def test_equity_identity_multi_symbol(self):
        """equity == cash + sum(position * price), exactly."""
        broker = PaperBroker(Decimal("5000"), fill_emulator=exact_emulator())
        broker.on_bar("SYM1", make_bar(ts=BAR_TS, close=100.0))
        broker.on_bar("SYM2", make_bar(ts=BAR_TS, close=50.0))
        broker.submit(MarketOrder("SYM1", 3))
        broker.submit(MarketOrder("SYM2", -2))
        self.assertEqual(broker.cash, Decimal("4800"))
        self.assertEqual(
            broker.equity({"SYM1": 120, "SYM2": 45}),
            Decimal("4800") + Decimal("360") - Decimal("90"),
        )
        with self.assertRaises(KeyError):  # a held symbol without a price is a loud error
            broker.equity({"SYM1": 120})

    def test_buying_power_refused_atomically(self):
        """A trade that would push cash negative is refused WHOLE."""
        broker = PaperBroker(Decimal("100"), fill_emulator=FillEmulator(seed=2, reject_rate=0.0))
        broker.on_bar("SYM1", make_bar(ts=BAR_TS, close=100.0))
        fills, refusal = broker.submit(MarketOrder("SYM1", 10))
        self.assertEqual(fills, [])
        self.assertIsNotNone(refusal)
        self.assertEqual(refusal.rule, "buying_power")
        self.assertEqual(broker.cash, Decimal("100"))
        self.assertEqual(broker.position("SYM1"), Decimal("0"))
        self.assertEqual(broker.trades, [])

    def test_equity_curve_strictly_increasing_ts(self):
        broker = PaperBroker(Decimal("1000"), fill_emulator=exact_emulator())
        t1, t2, t3 = ("2024-01-02T21:00:00+00:00", "2024-01-03T21:00:00+00:00", "2024-01-04T21:00:00+00:00")
        broker.on_bar("SYM1", make_bar(ts=t1, close=100.0))
        broker.on_bar("SYM1", make_bar(ts=t2, close=101.0))
        broker.submit(MarketOrder("SYM1", 1))  # same ts as t2: must not double-mark
        broker.on_bar("SYM1", make_bar(ts=t3, close=102.0))
        curve = broker.equity_curve
        self.assertEqual([ts for ts, _ in curve], [to_utc_ts(t) for t in (t1, t2, t3)])
        for (_, eq), equity_value in zip(curve, (Decimal("1000"), Decimal("1000"), Decimal("1001"))):
            self.assertIsInstance(eq, Decimal)
            self.assertEqual(eq, equity_value)

    def test_flat_fee_protocol(self):
        self.assertEqual(FlatFee(25).fee_for(Decimal("1000"), Decimal("10")), Decimal("2.5"))
        self.assertEqual(FlatFee(0).fee_for(Decimal("999999"), Decimal("1")), Decimal("0"))
        self.assertEqual(FlatFee(12.5).fee_for(Decimal("1000"), Decimal("10")), Decimal("1.25"))


# -- risk -----------------------------------------------------------------------


class TestRiskGuard(unittest.TestCase):
    def test_kill_switch_latches_forever(self):
        """A 25% drawdown blows the 15% limit; the latch then refuses everything,
        even a recovered account."""
        guard = RiskGuard(RiskConfig(max_drawdown=0.15))
        self.assertIsNone(guard.approve(MarketOrder("X", 1), acct(Decimal("100")), Decimal("10")))
        blown = guard.approve(MarketOrder("X", 1), acct(Decimal("75")), Decimal("10"))
        self.assertIsNotNone(blown)
        self.assertEqual(blown.rule, "max_drawdown")
        self.assertTrue(guard.latched)
        recovered = guard.approve(MarketOrder("X", 1), acct(Decimal("99")), Decimal("10"))
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.rule, "kill_switch")
        # A limit order, a tiny order, any order - the latch does not care.
        self.assertEqual(guard.approve(LimitOrder("X", 1, 10.0), acct(Decimal("99")), Decimal("10")).rule,
                         "kill_switch")

    def test_position_limit(self):
        guard = RiskGuard(RiskConfig(max_position_pct=0.25))
        a = acct(Decimal("1000"), cash=Decimal("1000"))
        too_big = guard.approve(MarketOrder("X", 30), a, Decimal("10"))  # 300 > 250
        self.assertEqual(too_big.rule, "position_limit")
        self.assertIsNone(guard.approve(MarketOrder("X", 20), a, Decimal("10")))  # 200 <= 250

    def test_fat_finger_price(self):
        guard = RiskGuard()  # fat_finger_price_mult = 5.0
        a = acct(Decimal("10000"), cash=Decimal("10000"))
        typo = guard.approve(LimitOrder("X", 1, 600.0), a, Decimal("100"))
        self.assertEqual(typo.rule, "fat_finger_price")
        self.assertIsNone(guard.approve(LimitOrder("X", 1, 400.0), a, Decimal("100")))

    def test_fat_finger_qty_learns_typical(self):
        """The first order in a symbol sets the baseline; 20x beyond it is a typo.
        Refused orders do not teach the guard what 'typical' means."""
        guard = RiskGuard()
        a = acct(Decimal("10000"), cash=Decimal("10000"))
        self.assertIsNone(guard.approve(MarketOrder("X", 1), a, Decimal("100")))
        self.assertEqual(guard.approve(MarketOrder("X", 25), a, Decimal("100")).rule, "fat_finger_qty")
        self.assertIsNone(guard.approve(MarketOrder("X", 20), a, Decimal("100")))  # exactly 20x: allowed

    def test_order_rate_cap_resets_per_bar(self):
        guard = RiskGuard(RiskConfig(max_orders_per_bar=3))
        a = acct(Decimal("10000"))
        guard.begin_bar(datetime(2024, 1, 2, 21, tzinfo=UTC))
        for _ in range(3):
            self.assertIsNone(guard.approve(MarketOrder("X", 1), a, Decimal("100")))
        self.assertEqual(guard.approve(MarketOrder("X", 1), a, Decimal("100")).rule, "order_rate")
        guard.begin_bar(datetime(2024, 1, 3, 21, tzinfo=UTC))
        self.assertIsNone(guard.approve(MarketOrder("X", 1), a, Decimal("100")))

    def test_unlatched_config_recovers(self):
        """kill_switch_latch=False: the breach refuses one order, then life goes on."""
        guard = RiskGuard(RiskConfig(max_drawdown=0.15, kill_switch_latch=False))
        self.assertIsNone(guard.approve(MarketOrder("X", 1), acct(Decimal("100")), Decimal("10")))
        refused = guard.approve(MarketOrder("X", 1), acct(Decimal("75")), Decimal("10"))
        self.assertEqual(refused.rule, "max_drawdown")
        self.assertFalse(guard.latched)
        self.assertIsNone(guard.approve(MarketOrder("X", 1), acct(Decimal("99")), Decimal("10")))


# -- replay -----------------------------------------------------------------------


class Momentum:
    """A small honest strategy: buy up-days, sell down-days, and fire one
    deliberately absurd order on `oversize_day` so the run exercises refusals."""

    def __init__(self, symbol: str, qty: int = 2, oversize_day: int = 5, oversize_qty: int = 100000):
        self.symbol = symbol
        self.qty = qty
        self.oversize_day = oversize_day
        self.oversize_qty = oversize_qty
        self.prev_close = None
        self.day = 0
        self.ctxs: list[Ctx] = []

    def on_bar(self, view, ctx) -> None:
        self.ctxs.append(ctx)
        try:
            bar = view.get(self.symbol)
        except (AsOfKeyError, LeakError):
            return
        close = bar["close"]
        if self.prev_close is not None:
            if self.day == self.oversize_day:
                q = self.oversize_qty
            elif close >= self.prev_close:
                q = self.qty
            else:
                q = -self.qty
            ctx.submit(MarketOrder(self.symbol, q))
        self.prev_close = close
        self.day += 1


class Peek:
    """A dishonest strategy: reads a symbol that does not exist yet."""

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol

    def on_bar(self, view, ctx) -> None:
        view.get(self.symbol)


class TestReplay(unittest.TestCase):
    def test_honest_strategy_end_to_end(self):
        """A view-only strategy runs over a fixture ledger with zero LeakErrors,
        a strictly-increasing Decimal equity curve, and cash that never goes
        negative."""
        led = price_ledger(("SYM1", "SYM2"), n_days=40, seed=5)
        clock = Clock(datetime(2024, 1, 1, 21, tzinfo=UTC))
        broker = PaperBroker(Decimal("10000"), fill_emulator=FillEmulator(seed=42, reject_rate=0.0))
        risk = RiskGuard()
        strategy = Momentum("SYM1")
        events: list[dict] = []
        replay = Replay(led, clock, strategy, broker, risk=risk, on_event=events.append)
        result = replay.run()

        self.assertEqual(result.n_bars, 40)
        self.assertEqual(result.n_fills, len(broker.trades))
        self.assertGreater(result.n_fills, 0)
        self.assertGreaterEqual(broker.cash, 0)
        self.assertEqual(len(result.equity_curve), result.n_bars)
        curve_ts = [ts for ts, _ in result.equity_curve]
        self.assertEqual(curve_ts, sorted(curve_ts))
        self.assertEqual(len(set(curve_ts)), len(curve_ts))  # strictly increasing
        for _, eq in result.equity_curve:
            self.assertIsInstance(eq, Decimal)
            self.assertGreaterEqual(eq, 0)
        rules = {r.rule for r in result.refusals}
        self.assertIn("fat_finger_qty", rules)  # the oversize order was caught
        self.assertEqual(len(result.refusals), sum(len(c.refusals) for c in strategy.ctxs))

    def test_peeking_strategy_raises_leak_error(self):
        """A strategy that asks the view for a symbol whose first bar is in the
        future trips the structural guard - loudly."""
        led = Ledger("prices")
        days = trading_days(datetime(2024, 1, 1, 21, tzinfo=UTC), 20)
        for i, day in enumerate(days):
            led.append("EARLY", make_bar(close=100.0), day, day)
            if i >= 10:
                led.append("LATE", make_bar(close=50.0), day, day)
        clock = Clock(days[0])
        broker = PaperBroker(Decimal("10000"), fill_emulator=exact_emulator())
        replay = Replay(led, clock, Peek("LATE"), broker, risk=RiskGuard())
        with self.assertRaises(LeakError):
            replay.run()

    def test_refusals_flow_to_ctx_and_result(self):
        led = price_ledger(("SYM1",), n_days=15, seed=6)
        clock = Clock(datetime(2024, 1, 1, 21, tzinfo=UTC))
        strategy = Momentum("SYM1", oversize_day=3)
        replay = Replay(
            led, clock, strategy,
            PaperBroker(Decimal("10000"), fill_emulator=exact_emulator()),
            risk=RiskGuard(),
        )
        result = replay.run()
        from_ctx = [r for c in strategy.ctxs for r in c.refusals]
        self.assertEqual(from_ctx, result.refusals)
        self.assertGreater(len(result.refusals), 0)
        for refusal in result.refusals:
            self.assertEqual(refusal.rule, "fat_finger_qty")

    def test_on_event_stream(self):
        led = price_ledger(("SYM1",), n_days=12, seed=6)
        clock = Clock(datetime(2024, 1, 1, 21, tzinfo=UTC))
        events: list[dict] = []
        replay = Replay(
            led, clock, Momentum("SYM1"),
            PaperBroker(Decimal("10000"), fill_emulator=exact_emulator()),
            risk=RiskGuard(),
            on_event=events.append,
        )
        result = replay.run()
        kinds = {e["type"] for e in events}
        self.assertIn("bar", kinds)
        self.assertIn("fill", kinds)
        self.assertIn("refusal", kinds)
        self.assertEqual(sum(1 for e in events if e["type"] == "fill"), result.n_fills)

    def test_clock_after_first_bar_raises(self):
        """Clock cannot rewind; Replay refuses rather than silently dropping
        the bars it cannot reach."""
        led = price_ledger(("SYM1",), n_days=5, seed=6)
        clock = Clock(datetime(2024, 1, 5, 21, tzinfo=UTC))  # after the first bar
        replay = Replay(
            led, clock, Momentum("SYM1"),
            PaperBroker(Decimal("10000"), fill_emulator=exact_emulator()),
        )
        with self.assertRaises(ValueError):
            replay.run()

    def test_empty_ledger_runs_to_an_empty_result(self):
        replay = Replay(
            Ledger("empty"), Clock(datetime(2024, 1, 1, tzinfo=UTC)), Momentum("SYM1"),
            PaperBroker(Decimal("1000"), fill_emulator=exact_emulator()),
        )
        result = replay.run()
        self.assertEqual(result.n_bars, 0)
        self.assertEqual(result.equity_curve, [])
        self.assertEqual(result.refusals, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
