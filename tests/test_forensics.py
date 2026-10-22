"""tests.test_forensics - forensics against hand-computed truth.

Every expected value is computed inside the test from raw arithmetic
(explicit loops, plain algebra, statistics.NormalDist) - never by calling
the module under test to build its own expectation.  Randomness is seeded
locally per test with random.Random(<literal>); nothing depends on the
wall clock, the global random module, or run order.
"""

import math
import random
import unittest
from statistics import NormalDist

from nohindsight.forensics import (
    FANTASY,
    REAL,
    SUSPICIOUS,
    Verdict,
    audit,
    calmar,
    deflated_psr,
    expected_max_sharpe,
    max_drawdown,
    moments,
    overfit_report,
    pbo,
    psr,
    sharpe,
    sortino,
    t_stat,
)


def _sharpe_example() -> list[float]:
    """A plain mixed-sign daily stream used by several hand-arithmetic tests."""
    return [0.01, 0.02, -0.005, 0.0, 0.015, 0.004, -0.002, 0.011]


def _flip_matrix() -> list[list[float]]:
    """Two configs that each win one regime and lose the other, exactly."""
    x = [0.1] * 4 + [-0.1] * 4
    y = [-0.1] * 4 + [0.1] * 4
    return [[a, b] for a, b in zip(x, y)]


def _border_matrix() -> list[list[float]]:
    """Three configs whose OOS winner lands exactly on the border rank.

    Block A Sharpes order configs 0 > 1 > 2; block B orders them 1 > 0 > 2
    (two-valued rows chosen so every Sharpe is distinct and exact).  Both
    train halves nominate a config that ranks EXACTLY 1 of 3 OOS -
    rel = 0.5, lam = 0 - so this pins the documented rule that the border
    counts as overfit.
    """
    block_a = [[0.03, 0.02, -0.01], [0.01, 0.0, -0.03]]
    block_b = [[0.02, 0.03, -0.01], [0.0, 0.01, -0.03]]
    return block_a + block_b


def _clear_winner_matrix() -> list[list[float]]:
    """Config 0 carries a drift so strong no block can dislodge it (seeded)."""
    rng = random.Random(5)
    rows = []
    for _ in range(80):
        row = [0.008 + 0.005 * rng.gauss(0.0, 1.0)]
        row += [0.005 * rng.gauss(0.0, 1.0) for _ in range(3)]
        rows.append(row)
    return rows


def _noise_matrix() -> list[list[float]]:
    """Four indistinguishable pure-noise configs (seeded, deterministic)."""
    rng = random.Random(17)
    return [[0.01 * rng.gauss(0.0, 1.0) for _ in range(4)] for _ in range(200)]


class SharpeTests(unittest.TestCase):
    def test_sharpe_matches_hand_arithmetic(self):
        rs = _sharpe_example()
        n = len(rs)
        mean = sum(rs) / n
        ss = sum((x - mean) ** 2 for x in rs)
        std = math.sqrt(ss / (n - 1))  # ddof=1, the documented convention
        expected = mean / std * math.sqrt(252)
        self.assertAlmostEqual(sharpe(rs), expected, places=10)

    def test_sharpe_degenerate_streams_settle_by_sign(self):
        self.assertEqual(sharpe([0.01] * 5), math.inf)
        self.assertEqual(sharpe([-0.01] * 5), -math.inf)
        self.assertEqual(sharpe([0.0] * 5), 0.0)

    def test_sharpe_needs_two_observations(self):
        with self.assertRaises(ValueError):
            sharpe([])
        with self.assertRaises(ValueError):
            sharpe([0.01])

    def test_sharpe_annualization_is_sqrt_ppy(self):
        rs = _sharpe_example()
        n = len(rs)
        mean = sum(rs) / n
        std = math.sqrt(sum((x - mean) ** 2 for x in rs) / (n - 1))
        self.assertAlmostEqual(sharpe(rs, 1), mean / std, places=10)
        self.assertAlmostEqual(
            sharpe(rs, 252), sharpe(rs, 1) * math.sqrt(252), places=10
        )


class SortinoTests(unittest.TestCase):
    def test_sortino_matches_hand_arithmetic(self):
        rs = [0.01, -0.02, 0.015, -0.005, 0.008, 0.0, -0.012, 0.02]
        n = len(rs)
        mean = sum(rs) / n
        dd = math.sqrt(sum(min(x, 0.0) ** 2 for x in rs) / n)  # full-sample 1/n
        expected = mean / dd * math.sqrt(252)
        self.assertAlmostEqual(sortino(rs), expected, places=10)

    def test_sortino_penalizes_concentrated_losses(self):
        # Same mean (both sum to 0.01 over 4 periods), very different
        # downside: rare deep losses must score a lower Sortino than the
        # same loss budget spread thin.
        spread = [0.005, 0.005, 0.005, -0.005]
        concentrated = [0.02, 0.02, -0.02, -0.01]
        self.assertAlmostEqual(sum(spread) / 4, sum(concentrated) / 4, places=12)
        self.assertGreater(sortino(spread), sortino(concentrated))


class DrawdownTests(unittest.TestCase):
    def test_max_drawdown_from_returns(self):
        # equity path: 1.1 -> 0.88 -> 0.968; trough vs peak = 1 - 0.88/1.1.
        rs = [0.1, -0.2, 0.1]
        expected = 1.0 - 1.1 * (1.0 + rs[1]) / 1.1  # written out, not called
        self.assertAlmostEqual(max_drawdown(rs), expected, places=12)
        self.assertAlmostEqual(max_drawdown(rs, kind="returns"), 0.2, places=12)

    def test_max_drawdown_from_equity_levels(self):
        levels = [100.0, 120.0, 90.0, 95.0]
        expected = 1.0 - 90.0 / 120.0  # written out: 0.25
        self.assertAlmostEqual(max_drawdown(levels), expected, places=12)
        self.assertAlmostEqual(
            max_drawdown(levels, kind="equity"), expected, places=12
        )

    def test_max_drawdown_auto_rule_is_documented(self):
        # [0.5, 0.4, 0.6] is genuinely ambiguous: auto reads all-|v|<1 as
        # returns (rising compounded curve, dd 0.0); forced equity reads the
        # levels and finds the 20% dip.  Both behaviors are the contract.
        fractional = [0.5, 0.4, 0.6]
        self.assertAlmostEqual(max_drawdown(fractional), 0.0, places=12)
        self.assertAlmostEqual(
            max_drawdown(fractional, kind="equity"), 1.0 - 0.4 / 0.5, places=12
        )

    def test_max_drawdown_trivial_inputs(self):
        self.assertEqual(max_drawdown([]), 0.0)
        self.assertEqual(max_drawdown([0.01]), 0.0)
        self.assertEqual(max_drawdown([1.0, 2.0, 3.0]), 0.0)


class CalmarTStatTests(unittest.TestCase):
    def test_calmar_hand_arithmetic(self):
        rs = [0.25, -0.2, 0.5, 0.0]
        growth = 1.25 * 0.8 * 1.5 * 1.0  # 1.5
        cagr = growth ** (4 / 4) - 1.0  # ppy=4, n=4 -> 0.5
        levels = [1.25, 1.0, 1.5, 1.5]
        dd = 1.0 - 1.0 / 1.25  # peak 1.25, trough 1.0 -> 0.2
        self.assertAlmostEqual(cagr / dd, 2.5, places=12)
        self.assertAlmostEqual(calmar(rs, 4), 2.5, places=10)

    def test_calmar_no_drawdown_settles_by_sign(self):
        self.assertEqual(calmar([0.01, 0.02, 0.005]), math.inf)
        self.assertEqual(calmar([0.0, 0.0, 0.0]), 0.0)

    def test_t_stat_matches_hand_arithmetic(self):
        rs = _sharpe_example()
        n = len(rs)
        mean = sum(rs) / n
        std = math.sqrt(sum((x - mean) ** 2 for x in rs) / (n - 1))
        expected = mean / (std / math.sqrt(n))
        self.assertAlmostEqual(t_stat(rs), expected, places=10)


class MomentsTests(unittest.TestCase):
    def test_moments_match_hand_arithmetic(self):
        rs = [1.0, 2.0, 3.0, 4.0]
        n = len(rs)
        mean = sum(rs) / n
        m2 = sum((x - mean) ** 2 for x in rs) / n  # 1.25
        m3 = sum((x - mean) ** 3 for x in rs) / n  # 0.0 (symmetric)
        m4 = sum((x - mean) ** 4 for x in rs) / n  # 2.5625
        mean_v, std_v, skew_v, kurt_v = moments(rs)
        self.assertAlmostEqual(mean_v, mean, places=12)
        self.assertAlmostEqual(std_v, math.sqrt(m2), places=12)
        self.assertAlmostEqual(skew_v, m3 / m2**1.5, places=12)
        self.assertAlmostEqual(kurt_v, m4 / m2**2, places=12)  # Pearson: 1.64

    def test_moments_constant_stream_is_shapeless(self):
        self.assertEqual(moments([7.0] * 9), (7.0, 0.0, 0.0, 3.0))


class PSRTests(unittest.TestCase):
    def test_psr_worked_example(self):
        rs = [0.012, -0.004, 0.007, 0.015, -0.009, 0.003, 0.011,
              -0.002, 0.006, -0.007, 0.009, 0.001]
        n = len(rs)
        # Independent arithmetic, written out: annualized SR (ddof=1) and
        # population skew/kurtosis, then the PSR map assembled by hand.
        mean = sum(rs) / n
        ss = sum((x - mean) ** 2 for x in rs)
        sr = mean / math.sqrt(ss / (n - 1)) * math.sqrt(252)
        m2 = ss / n
        m3 = sum((x - mean) ** 3 for x in rs) / n
        m4 = sum((x - mean) ** 4 for x in rs) / n
        g3 = m3 / m2**1.5
        g4 = m4 / m2**2
        for benchmark in (0.0, 4.0):
            var_term = 1.0 - g3 * sr + ((g4 - 1.0) / 4.0) * sr * sr
            denom = max(
                math.sqrt(var_term) if var_term > 0.0 else 1e-12, 1e-12
            )
            z = (sr - benchmark) * math.sqrt(n - 1) / denom
            expected = NormalDist().cdf(z)
            self.assertAlmostEqual(psr(rs, benchmark), expected, places=10)
        # Monotone in the benchmark, with sign anchors around SR.
        self.assertGreater(psr(rs, 0.0), psr(rs, 4.0))
        self.assertGreater(psr(rs, 4.0), psr(rs, 8.0))
        self.assertGreater(psr(rs, 0.0), 0.5)
        self.assertLess(psr(rs, 8.0), 0.5)

    def test_psr_monotone_in_sample_size(self):
        # Alternating two-valued stream: exact mean/std per length, so the
        # ONLY thing that changes with n is the sqrt(n-1) evidence term.
        pattern = [0.010126, -0.009874]
        short, long_ = pattern * 10, pattern * 30
        p_short, p_long = psr(short, 0.0), psr(long_, 0.0)
        self.assertAlmostEqual(p_short, 0.8023, places=3)
        self.assertAlmostEqual(p_long, 0.9362, places=3)
        self.assertLess(p_short, p_long)

    def test_psr_skew_penalty(self):
        # Mirror construction: identical mean and std, opposite skew.  The
        # negative-skew twin must score the lower PSR against the same
        # benchmark - that is the whole point of the correction.
        c, d = 0.001, 0.01
        pos = [c - d] * 3 + [c + 3 * d]
        neg = [c + d] * 3 + [c - 3 * d]
        mean_pos, mean_neg = sum(pos) / 4, sum(neg) / 4
        self.assertAlmostEqual(mean_pos, mean_neg, places=15)
        std_pos = math.sqrt(sum((x - mean_pos) ** 2 for x in pos) / 3)
        std_neg = math.sqrt(sum((x - mean_neg) ** 2 for x in neg) / 3)
        self.assertAlmostEqual(std_pos, std_neg, places=15)
        self.assertGreater(psr(pos, 0.0) - psr(neg, 0.0), 0.05)


class ExpectedMaxTests(unittest.TestCase):
    def test_expected_max_sharpe_matches_formula(self):
        rng = random.Random(2024)
        trials = [rng.gauss(0.0, 1.0) for _ in range(24)]
        n = len(trials)
        mean = sum(trials) / n
        variance = sum((x - mean) ** 2 for x in trials) / n  # population form
        gamma = 0.5772156649015329
        z_best = NormalDist().inv_cdf(1.0 - 1.0 / n)
        z_lucky = NormalDist().inv_cdf(1.0 - 1.0 / (n * math.e))
        expected = math.sqrt(variance) * (
            (1.0 - gamma) * z_best + gamma * z_lucky
        )
        self.assertAlmostEqual(expected_max_sharpe(trials), expected, places=10)

    def test_expected_max_sharpe_grows_with_trials(self):
        rng = random.Random(11)
        base = [rng.gauss(0.0, 1.0) for _ in range(48)]

        def standardized(xs):
            m = sum(xs) / len(xs)
            sd = math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))
            return [(x - m) / sd for x in xs]

        t12, t48 = standardized(base[:12]), standardized(base)
        # Both prefixes carry ~unit variance, so growth is pure selection.
        self.assertLess(expected_max_sharpe(t12), expected_max_sharpe(t48))

    def test_expected_max_sharpe_scales_with_variance(self):
        rng = random.Random(11)
        trials = [rng.gauss(0.0, 1.0) for _ in range(48)]
        base = expected_max_sharpe(trials)
        scaled = expected_max_sharpe([3.0 * x for x in trials])
        self.assertAlmostEqual(scaled, 3.0 * base, delta=1e-9)

    def test_expected_max_sharpe_edges(self):
        self.assertEqual(expected_max_sharpe([2.0]), 2.0)
        self.assertEqual(expected_max_sharpe([5.0] * 7), 5.0)
        with self.assertRaises(ValueError):
            expected_max_sharpe([])


class DeflatedPSRTests(unittest.TestCase):
    def test_deflated_psr_single_trial_collapses_to_psr(self):
        rs = [0.010126, -0.009874] * 25
        self.assertAlmostEqual(deflated_psr(rs, 1), psr(rs, 0.0), places=12)

    def test_deflated_psr_trials_raise_the_hurdle(self):
        rs = [0.010126, -0.009874] * 25
        rng = random.Random(7)
        trials = [rng.gauss(0.0, 1.0) for _ in range(24)]
        dsr_one = deflated_psr(rs, 1)
        self.assertLess(deflated_psr(rs, 40), dsr_one)  # count-only fallback
        self.assertLess(
            deflated_psr(rs, len(trials), trial_sharpes=trials), dsr_one
        )

    def test_deflated_psr_zero_variance_trials_pin_the_benchmark(self):
        # Identical trials all tie, so the expected max IS the common value:
        # the deflated PSR must collapse onto the plain PSR at that benchmark.
        rs = [0.010126, -0.009874] * 25
        self.assertAlmostEqual(
            deflated_psr(rs, 40, trial_sharpes=[0.0] * 40),
            psr(rs, 0.0),
            places=12,
        )
        self.assertAlmostEqual(
            deflated_psr(rs, 40, trial_sharpes=[1.5] * 40),
            psr(rs, 1.5),
            places=12,
        )


class PBOTests(unittest.TestCase):
    def test_pbo_clear_winner_stays_winner(self):
        # Config 0's drift dominates every block, IS and OOS alike, so it is
        # never caught in the worse half: PBO exactly 0 over all 70 splits.
        self.assertEqual(pbo(_clear_winner_matrix(), blocks=8), 0.0)

    def test_pbo_regime_flip_overfits_everything(self):
        report = overfit_report(_flip_matrix(), blocks=2)
        self.assertEqual(report["pbo"], 1.0)
        self.assertEqual(report["n_combos"], 2)
        self.assertEqual(report["is_best_oos_median_rank"], 1.0)

    def test_pbo_noise_lands_near_half(self):
        # Indistinguishable configs: the IS winner is a coin flip OOS, so
        # PBO crowds 0.5.  252 exhaustive splits put the sd near 0.031, so
        # the asserted band is several sd wide on either side.
        report = overfit_report(_noise_matrix(), blocks=10)
        self.assertGreaterEqual(report["pbo"], 0.3)
        self.assertLessEqual(report["pbo"], 0.7)
        self.assertEqual(report["n_combos"], 252)  # C(10, 5), exhaustive
        self.assertGreaterEqual(report["is_best_oos_median_rank"], 0.0)
        self.assertLessEqual(report["is_best_oos_median_rank"], 3.0)

    def test_pbo_border_rank_counts_as_overfit(self):
        # Both splits land at exactly rel = 0.5 (lam = 0); counting the
        # border is the documented conservative choice - without it this
        # would read 0.0 instead of 1.0.
        self.assertEqual(pbo(_border_matrix(), blocks=2), 1.0)

    def test_pbo_validates_input(self):
        with self.assertRaises(ValueError):
            pbo([[0.01, 0.02], [0.01]])  # ragged
        with self.assertRaises(ValueError):
            pbo([[0.01, 0.02]] * 5, blocks=4)  # 1 row per block < 2
        with self.assertRaises(ValueError):
            pbo([[0.01, 0.02]] * 8, blocks=3)  # odd blocks
        with self.assertRaises(ValueError):
            pbo([[0.01]] * 8, blocks=2)  # single config
        with self.assertRaises(ValueError):
            pbo([])

    def test_pbo_flat_stream_gets_a_helpful_error(self):
        # The likeliest caller mistake: a return stream where a matrix
        # belongs.  The error must say so, not mumble about iterables.
        with self.assertRaises(TypeError):
            pbo([0.01, -0.01, 0.02])


class AuditTests(unittest.TestCase):
    def test_audit_real_on_genuine_drift(self):
        rng = random.Random(42)
        drift = [0.0015 + 0.006 * rng.gauss(0.0, 1.0) for _ in range(300)]
        verdict = audit(drift, n_trials=1, mc_paths=400, seed=3)
        self.assertEqual(verdict.level, REAL)
        self.assertIsNone(verdict.numbers["pbo"])
        self.assertGreaterEqual(verdict.numbers["dsr_prob"], 0.95)
        self.assertLess(verdict.numbers["p_value"], 0.05)

    def test_audit_fantasy_on_zero_edge(self):
        # Perfectly alternating +/− returns: Sharpe exactly 0, so the DSR
        # probability sits exactly on 0.5 and the permutation test (roughly
        # half of no-edge paths tie or beat it) carries the FANTASY call.
        verdict = audit([0.01, -0.01] * 150, n_trials=1, mc_paths=400, seed=1)
        self.assertEqual(verdict.level, FANTASY)
        self.assertAlmostEqual(verdict.numbers["dsr_prob"], 0.5, places=12)
        self.assertGreaterEqual(verdict.numbers["p_value"], 0.2)

    def test_audit_suspicious_in_between(self):
        # Two-valued stream with t ~ 1.14: too strong for FANTASY's bars,
        # not strong enough for REAL's permutation gate.
        two_valued = [0.011] * 33 + [-0.010] * 27
        verdict = audit(two_valued, n_trials=1, mc_paths=400, seed=9)
        self.assertEqual(verdict.level, SUSPICIOUS)
        self.assertGreaterEqual(verdict.numbers["p_value"], 0.05)
        self.assertLess(verdict.numbers["p_value"], 0.2)
        self.assertGreaterEqual(verdict.numbers["dsr_prob"], 0.5)

    def test_audit_pbo_gate_keeps_fantasy_honest(self):
        rng = random.Random(42)
        drift = [0.0015 + 0.006 * rng.gauss(0.0, 1.0) for _ in range(300)]
        with_family = audit(
            drift, n_trials=1, strategy_returns=_clear_winner_matrix(),
            blocks=8, mc_paths=200, seed=3,
        )
        self.assertEqual(with_family.level, REAL)
        self.assertEqual(with_family.numbers["pbo"], 0.0)
        # Same lovely stream, but it came from a family whose IS winner
        # flips regime OOS: the broken selection process sinks the verdict.
        from_flipped_family = audit(
            drift, n_trials=1, strategy_returns=_flip_matrix(),
            blocks=2, mc_paths=200, seed=3,
        )
        self.assertEqual(from_flipped_family.level, FANTASY)
        self.assertEqual(from_flipped_family.numbers["pbo"], 1.0)
        self.assertTrue(any("PBO" in r for r in from_flipped_family.reasons))

    def test_audit_numbers_dict_is_complete(self):
        verdict = audit([0.010126, -0.009874] * 25, n_trials=1, mc_paths=50)
        expected_keys = {
            "sharpe", "sortino", "calmar", "max_drawdown", "t_stat",
            "skew", "kurtosis", "n_obs", "n_trials", "sr0", "dsr_prob",
            "p_value", "mc_paths", "pbo",
        }
        self.assertLessEqual(expected_keys, set(verdict.numbers))
        for value in verdict.numbers.values():
            self.assertTrue(value is None or isinstance(value, (int, float)))
        self.assertIsInstance(verdict, Verdict)

    def test_audit_reasons_cite_their_numbers(self):
        rng = random.Random(42)
        drift = [0.0015 + 0.006 * rng.gauss(0.0, 1.0) for _ in range(300)]
        verdict = audit(drift, n_trials=1, mc_paths=400, seed=3)
        self.assertGreaterEqual(len(verdict.reasons), 4)
        # Every evidence reason carries the number it argues about; the
        # closing sentence is the only one allowed to be prose-only.
        for reason in verdict.reasons[:-1]:
            self.assertTrue(any(ch.isdigit() for ch in reason), reason)
        self.assertIn(f"{verdict.numbers['sharpe']:.2f}", " ".join(verdict.reasons))

    def test_audit_rejects_thin_input(self):
        with self.assertRaises(ValueError):
            audit([])
        with self.assertRaises(ValueError):
            audit([0.01])
        with self.assertRaises(ValueError):
            audit([0.01, -0.01], n_trials=0)
        with self.assertRaises(ValueError):
            audit([0.01, -0.01], mc_paths=0)


if __name__ == "__main__":
    unittest.main()
