"""tests.test_costs — the reality tax, itemized and argued with.

Every expected number is written as an arithmetic expression mirroring
the documented convention (not a bare constant), so a reader can check
the convention against the math in one glance.  No randomness: cost
pricing is deterministic by construction.
"""

import math
import unittest

from nohindsight.costs import (
    BorrowModel,
    CostBreakdown,
    CostModel,
    FeeSchedule,
    ImpactModel,
    SpreadModel,
    Trade,
    fantasy_vs_real,
)


def _trade(**over) -> Trade:
    base = dict(
        ts="2024-03-04T15:00:00+00:00",
        symbol="SYM1",
        side="buy",
        price=100.0,
        qty=10.0,
    )
    base.update(over)
    return Trade(**base)


# Hand-built return series, deterministic.  POSITIVE sums to 0.0034 over
# 12 periods (mean ~2.83e-4); NEGATIVE is its negation.
POSITIVE = [
    0.0012, -0.0003, 0.0009, -0.0005,
    0.0007, -0.0002, 0.0011, -0.0004,
    0.0008, -0.0006, 0.0010, -0.0003,
]
NEGATIVE = [-r for r in POSITIVE]


class FeeScheduleTests(unittest.TestCase):
    def test_flat_bps_is_bps_of_notional(self):
        # 10 bps of $1,000,000 is $1,000 — notional-proportional, shares
        # irrelevant.
        self.assertEqual(FeeSchedule.flat_bps(10.0).apply(1_000_000.0, 100), 1000.0)

    def test_per_share_ignores_notional(self):
        # 0.5 cents x 1000 shares = $5.00 — size-proportional, notional
        # irrelevant.
        self.assertEqual(FeeSchedule.per_share(0.5).apply(0.0, 1000), 5.0)

    def test_zero_is_zero(self):
        self.assertEqual(FeeSchedule.zero().apply(1e9, 1e6), 0.0)


class SpreadModelTests(unittest.TestCase):
    def test_half_spread_per_crossing(self):
        # constant(20): full spread 20 bps, one crossing pays HALF —
        # 10 bps of the $1,000 notional = 100 * 10 * 10 / 1e4 = 1.00.
        self.assertAlmostEqual(SpreadModel.constant(20.0).half_cost(100.0, 10.0), 1.00, places=12)

    def test_round_trip_pays_exactly_the_full_spread(self):
        # Two Trade records (entry + exit), each one crossing, each one
        # half-spread: together exactly the full 20 bps, once.
        buy = SpreadModel.constant(20.0).half_cost(100.0, 10.0)
        sell = SpreadModel.constant(20.0).half_cost(100.0, 10.0)
        full_spread = 100.0 * 10.0 * 20.0 / 1e4
        self.assertAlmostEqual(buy + sell, full_spread, places=12)

    def test_volatility_scaled_widens_with_sigma_and_falls_back_to_base(self):
        # base 10 bps, mult 8: sigma 100 bps -> 10 * (1 + 8*100/1000) = 18
        # bps full, 9 bps half -> on the $10,000 notional: 200*50*9/1e4 = 9.00.
        model = SpreadModel.volatility_scaled(10.0, mult=8.0)
        self.assertAlmostEqual(model.half_cost(200.0, 50.0, sigma_bps=100.0), 9.00, places=12)
        # No sigma -> base spread, not zero: 10 bps full, 5 half ->
        # 200 * 50 * 5 / 1e4 = 5.00.
        self.assertAlmostEqual(model.half_cost(200.0, 50.0), 5.00, places=12)


class ImpactModelTests(unittest.TestCase):
    def test_sqrt_law_matches_formula(self):
        # coeff * sigma_bps * sqrt(qty/adv) = 1.0 * 80 * sqrt(1000/1e6).
        model = ImpactModel.sqrt_law(coeff=1.0)
        expected = 80.0 * math.sqrt(1000.0 / 1_000_000.0)
        self.assertAlmostEqual(model.impact_bps(1000.0, 1_000_000.0, 80.0), expected, places=12)

    def test_sqrt_law_quadruple_qty_doubles_impact(self):
        model = ImpactModel.sqrt_law(coeff=1.0)
        small = model.impact_bps(1000.0, 1_000_000.0, 80.0)
        large = model.impact_bps(4000.0, 1_000_000.0, 80.0)
        self.assertAlmostEqual(large, 2.0 * small, places=12)

    def test_missing_inputs_give_zero_but_unexplained(self):
        model = ImpactModel.sqrt_law(coeff=1.0)
        # No ADV -> cannot estimate.
        self.assertEqual(model.estimate(1000.0, None, 80.0), (0.0, False))
        # sqrt law without sigma -> cannot estimate.
        self.assertEqual(model.estimate(1000.0, 1_000_000.0, None), (0.0, False))
        # All inputs present and qty 0 -> a real, explained zero.
        self.assertEqual(model.estimate(0.0, 1_000_000.0, 80.0), (0.0, True))

    def test_linear_ignores_sigma(self):
        # eta * participation = 0.1 * (20000/1e6) = 0.002 bps; the absurd
        # sigma proves the linear form never looks at it.
        model = ImpactModel.linear(eta=0.1)
        self.assertAlmostEqual(model.impact_bps(20_000.0, 1_000_000.0, 999.0), 0.002, places=12)


class BorrowModelTests(unittest.TestCase):
    def test_proration(self):
        # $1M short for 30 days at 5% annual: 1e6 * 0.05 * 30/365.
        cost = BorrowModel.annual(0.05).cost(1_000_000.0, 30.0)
        self.assertEqual(cost, 1_000_000.0 * 0.05 * 30.0 / 365.0)

    def test_shorts_pay_buys_do_not(self):
        model = CostModel(
            spread=SpreadModel.constant(0.0),
            borrow=BorrowModel.annual(0.05),
        )
        short = model.price(_trade(side="sell", holding_days=10.0))
        self.assertEqual(short.borrow_cost, 1000.0 * 0.05 * 10.0 / 365.0)  # notional 100*10
        long_ = model.price(_trade(side="buy", holding_days=10.0))
        self.assertEqual(long_.borrow_cost, 0.0)


class CostModelTests(unittest.TestCase):
    def test_composition_sums(self):
        # Each line must equal what its sub-model charges independently;
        # the total must be their sum; the rate must be total/notional.
        model = CostModel(
            fees=FeeSchedule.flat_bps(2.0),
            spread=SpreadModel.constant(12.0),
            impact=ImpactModel.sqrt_law(coeff=1.0),
            borrow=BorrowModel.annual(0.05),
        )
        trade = _trade(side="sell", price=50.0, qty=2000.0, sigma_bps=50.0,
                       adv=2_000_000.0, holding_days=7.0)
        b = model.price(trade)

        notional = 50.0 * 2000.0  # 100,000
        self.assertEqual(b.notional, notional)
        self.assertEqual(b.fee, notional * 2.0 / 1e4)                       # 20.0
        self.assertAlmostEqual(b.spread_cost, notional * 6.0 / 1e4, places=12)  # half of 12 bps
        expected_impact_bps = 50.0 * math.sqrt(2000.0 / 2_000_000.0)
        self.assertAlmostEqual(b.impact_cost, notional * expected_impact_bps / 1e4, places=9)
        self.assertEqual(b.borrow_cost, notional * 0.05 * 7.0 / 365.0)
        self.assertAlmostEqual(
            b.total,
            b.fee + b.spread_cost + b.impact_cost + b.borrow_cost,
            places=12,
        )
        self.assertAlmostEqual(b.total_bps, b.total / notional * 1e4, places=12)

    def test_total_bps_scale_and_sign(self):
        # A 1 bp fee and nothing else prices at almost exactly 1 bp, and
        # never negative.
        model = CostModel(fees=FeeSchedule.flat_bps(1.0), spread=SpreadModel.constant(0.0))
        b = model.price(_trade(price=250.0, qty=40.0))
        self.assertGreaterEqual(b.total_bps, 0.0)
        self.assertAlmostEqual(b.total_bps, 1.0, places=9)

    def test_aggregate_total_bps_is_notional_weighted(self):
        model = CostModel(fees=FeeSchedule.flat_bps(1.0), spread=SpreadModel.constant(0.0))
        big = _trade(price=1000.0, qty=1000.0)   # notional 1,000,000 @ ~1bp
        small = _trade(symbol="SYM2", price=1.0, qty=10.0)  # notional 10 @ ~1bp
        expected = (model.price(big).total + model.price(small).total) / 1_000_010.0 * 1e4
        self.assertAlmostEqual(model.total_bps([big, small]), expected, places=9)
        # ...which is ~1bp, NOT the arithmetic mean of two ~1bp trades'
        # anything; and on a 100bp spread the weighting shows:
        spread_heavy = CostModel(spread=SpreadModel.constant(200.0))
        self.assertAlmostEqual(
            spread_heavy.total_bps([big, small]),
            (spread_heavy.price(big).total + spread_heavy.price(small).total) / 1_000_010.0 * 1e4,
            places=9,
        )

    def test_impact_unexplained_flag_propagates_to_breakdown(self):
        model = CostModel(spread=SpreadModel.constant(0.0), impact=ImpactModel.sqrt_law())
        unexplained = model.price(_trade())  # no sigma, no adv
        self.assertEqual(unexplained.impact_cost, 0.0)
        self.assertFalse(unexplained.impact_explained)
        explained = model.price(_trade(sigma_bps=80.0, adv=1_000_000.0))
        self.assertGreater(explained.impact_cost, 0.0)
        self.assertTrue(explained.impact_explained)

    def test_apply_matches_price(self):
        model = CostModel(
            fees=FeeSchedule.per_share(0.3),
            spread=SpreadModel.volatility_scaled(8.0),
            impact=ImpactModel.linear(),
            borrow=BorrowModel.annual(0.05),
        )
        trades = [
            _trade(),
            _trade(side="sell", price=52.0, qty=10.0, sigma_bps=90.0,
                   adv=500_000.0, holding_days=3.0),
        ]
        self.assertEqual(model.apply(trades), [model.price(t) for t in trades])

    def test_empty_breakdown_is_safe(self):
        b = CostBreakdown()
        self.assertEqual(b.total, 0.0)
        self.assertEqual(b.total_bps, 0.0)  # zero notional: no ZeroDivisionError


class TradeTests(unittest.TestCase):
    def test_validation(self):
        with self.assertRaises(ValueError):
            _trade(side="long")  # typo'd side
        with self.assertRaises(ValueError):
            _trade(qty=-5.0)  # direction lives in side, not the sign of qty
        with self.assertRaises(ValueError):
            _trade(price=0.0)
        with self.assertRaises(ValueError):
            _trade(holding_days=-1.0)


class FantasyVsRealTests(unittest.TestCase):
    def test_zero_cost_identity(self):
        # No drag: after-cost Sharpe IS the before-cost Sharpe, exactly,
        # and nothing shrank.
        result = fantasy_vs_real(POSITIVE, costs_bps=0.0, periods_per_year=252)
        self.assertEqual(result["sharpe_after"], result["sharpe_before"])
        self.assertEqual(result["shrinkage"], 1.0)

    def test_sharpe_before_is_invariant_under_costs(self):
        r0 = fantasy_vs_real(POSITIVE, costs_bps=0.0, periods_per_year=252)
        r50 = fantasy_vs_real(POSITIVE, costs_bps=50.0, periods_per_year=252)
        self.assertEqual(r50["sharpe_before"], r0["sharpe_before"])

    def test_monotone_in_costs(self):
        after = []
        shrink = []
        for costs in (0.0, 1.0, 5.0, 10.0, 50.0, 200.0):
            r = fantasy_vs_real(POSITIVE, costs_bps=costs, periods_per_year=252)
            after.append(r["sharpe_after"])
            shrink.append(r["shrinkage"])
        for i in range(len(after) - 1):
            self.assertLessEqual(after[i + 1], after[i])
            self.assertLessEqual(shrink[i + 1], shrink[i])

    def test_drag_kills_a_small_edge(self):
        # Mean edge 1 bp per period (0.0009/-0.0007 alternating), drag
        # 2 bps per period: profitable fantasy, losing reality.  This is
        # the whole point of the function.
        edge = [0.0009, -0.0007] * 10  # mean = +0.0001
        r = fantasy_vs_real(edge, costs_bps=2.0, periods_per_year=252)
        self.assertGreater(r["sharpe_before"], 0.0)
        self.assertLess(r["sharpe_after"], 0.0)

    def test_losing_strategy_shrinks_upward(self):
        # Costs hurt bad strategies too: after < before < 0, so the
        # "shrinkage" ratio exceeds 1 — honestly reported, not clamped.
        r = fantasy_vs_real(NEGATIVE, costs_bps=0.5, periods_per_year=252)
        self.assertLess(r["sharpe_after"], r["sharpe_before"])
        self.assertLess(r["sharpe_before"], 0.0)
        self.assertGreater(r["shrinkage"], 1.0)

    def test_needs_at_least_two_returns(self):
        with self.assertRaises(ValueError):
            fantasy_vs_real([0.01], costs_bps=1.0, periods_per_year=252)


if __name__ == "__main__":
    unittest.main()
