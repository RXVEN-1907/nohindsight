"""tests.test_checks — the police, cross-examined.

Every fixture is hand-built and every expected value is written where a
reader can check it against the documented convention.  There is no
randomness anywhere in this file: nothing is seeded because nothing is
drawn — the checks and the fixtures are deterministic by construction.

Fixture dates live in January 2024 (Jan 1 is a Monday), so the weekday
arithmetic in gap tests is checkable by eye.
"""

import unittest
from datetime import date, timedelta

from nohindsight.checks import (
    CheckReport,
    Finding,
    LeakTrap,
    Severity,
    assert_no_future_access,
    check_crossed,
    check_duplicates,
    check_gaps,
    check_ohlc,
    check_scale_shock,
    check_stale,
    detect_splits,
    run_all,
    scan_source,
    summarize,
)
from nohindsight.core import Clock, Ledger, LeakError, to_utc

# A gentle sawtooth: every return is +/-0.00995, so the trailing sigma
# is 0.00995 and ordinary bars sit at 1.0 z — miles from the z=8 line.
SAWTOOTH = [100.0, 101.0]


def _weekday_dates(n: int, start: str = "2024-01-02") -> list[str]:
    """n consecutive weekday ISO dates starting at `start` (inclusive)."""
    d = date.fromisoformat(start)
    out: list[str] = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _bars(closes, volumes=None, dates=None, **fields) -> list[dict]:
    """Build bars with flat geometry (open=high=low=close) so every check
    except the one under test has nothing to say."""
    dates = _weekday_dates(len(closes)) if dates is None else dates
    volumes = [1000.0] * len(closes) if volumes is None else volumes
    bars = []
    for ts, close, vol in zip(dates, closes, volumes):
        bar = {
            "ts": ts,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": vol,
        }
        bar.update(fields)
        bars.append(bar)
    return bars


class BaseTests(unittest.TestCase):
    def test_severity_is_ordered(self):
        # The order is load-bearing (summary tables sort by it).
        self.assertLess(Severity.INFO, Severity.WARN)
        self.assertLess(Severity.WARN, Severity.LEAK)

    def test_counts_by_severity_and_truthiness(self):
        report = CheckReport()
        report.add(Finding("A", Severity.LEAK, "m"))
        report.add(Finding("B", Severity.WARN, "m"))
        report.add(Finding("C", Severity.WARN, "m"))
        self.assertEqual(
            report.counts(),
            {Severity.INFO: 0, Severity.WARN: 2, Severity.LEAK: 1},
        )
        self.assertTrue(report.has_leaks)
        self.assertTrue(report)  # bool(report) == has_leaks

    def test_empty_report_is_falsy_and_leak_free(self):
        report = CheckReport()
        self.assertEqual(report.findings, [])
        self.assertEqual(
            report.counts(),
            {Severity.INFO: 0, Severity.WARN: 0, Severity.LEAK: 0},
        )
        self.assertFalse(report.has_leaks)
        self.assertFalse(report)


class QualityTests(unittest.TestCase):
    def setUp(self):
        self.clean = _bars(SAWTOOTH * 15)  # 30 clean bars

    def test_clean_bars_are_clean_everywhere(self):
        for check in (
            check_ohlc,
            check_duplicates,
            check_gaps,
            check_stale,
            check_crossed,
            check_scale_shock,
        ):
            self.assertEqual(check(self.clean), [], f"{check.__name__} flagged clean bars")

    def test_ohlc_violation_is_one_leak_pointing_at_the_bar(self):
        bars = _bars([100.0, 101.0, 102.0])
        bars[1]["high"] = 100.5  # high < close (101.0) — impossible geometry
        findings = check_ohlc(bars)
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.code, "OHLC_INVALID")
        self.assertEqual(finding.severity, Severity.LEAK)
        self.assertEqual(finding.where, "bars[1]")

    def test_ohlc_nan_and_nonpositive_low(self):
        bars = _bars([100.0, 101.0, 102.0, 103.0])
        bars[0]["close"] = float("nan")  # not finite
        bars[2]["low"] = 0.0             # low <= 0
        findings = check_ohlc(bars)
        self.assertEqual(len(findings), 2)
        self.assertEqual({f.where for f in findings}, {"bars[0]", "bars[2]"})
        self.assertTrue(all(f.severity == Severity.LEAK for f in findings))

    def test_duplicate_ts_is_one_finding(self):
        dates = _weekday_dates(4)
        bars = _bars([100.0, 101.0, 102.0, 103.0], dates=dates)
        bars[2]["ts"] = bars[1]["ts"]
        findings = check_duplicates(bars)
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.code, "DUPLICATE_TS")
        self.assertEqual(finding.severity, Severity.LEAK)
        self.assertEqual(finding.detail["indices"], [1, 2])

    def test_weekend_is_not_a_gap(self):
        # Fri 2024-01-05 -> Mon 2024-01-08: one weekday of spacing, same
        # as any healthy overnight.
        dates = ["2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08", "2024-01-09"]
        bars = _bars([100.0] * 5, dates=dates)
        self.assertEqual(check_gaps(bars), [])

    def test_long_closure_is_one_gap_warning(self):
        # Fri 2024-01-05 -> Mon 2024-01-15: six weekdays of spacing
        # (Jan 8-12, then Jan 15) — more than max_gap_days=5.
        dates = ["2024-01-04", "2024-01-05", "2024-01-15", "2024-01-16"]
        bars = _bars([100.0] * 4, dates=dates)
        findings = check_gaps(bars)
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.code, "CALENDAR_GAP")
        self.assertEqual(finding.severity, Severity.WARN)
        self.assertEqual(finding.detail["gap_weekdays"], 6)
        self.assertEqual(finding.detail["expected_days"], 1)

    def test_stale_run_threshold_and_maximal_runs(self):
        run_of_three = _bars([100.0, 101.0, 102.0, 102.0, 102.0, 103.0])
        self.assertEqual(check_stale(run_of_three), [])  # 3 < run=4

        run_of_four = _bars([100.0, 101.0, 102.0, 102.0, 102.0, 102.0, 103.0])
        findings = check_stale(run_of_four)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].code, "STALE_CLOSES")
        self.assertEqual(findings[0].severity, Severity.WARN)
        self.assertEqual(findings[0].detail["length"], 4)

        run_of_six = _bars([100.0] * 6)
        self.assertEqual(len(check_stale(run_of_six)), 1)  # one maximal run

    def test_crossed_market(self):
        bars = _bars([100.0, 101.0, 102.0])
        self.assertEqual(check_crossed(bars), [])  # no bid/ask: not its business
        bars[1]["bid"], bars[1]["ask"] = 101.5, 101.0
        findings = check_crossed(bars)
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.code, "CROSSED_MARKET")
        self.assertEqual(finding.severity, Severity.LEAK)
        self.assertEqual(finding.where, "bars[1]")

    def test_scale_shock_fires_exactly_once(self):
        # 40 sawtooth bars, one +48.5% jump (ln(150/101) = +0.396 = 39.8
        # sigma of the +/-0.00995 sawtooth), then quiet bars whose window
        # now contains the shock — their sigma is inflated, so they stay
        # under the line.
        closes = SAWTOOTH * 20 + [150.0, 151.0, 150.0, 151.0]
        bars = _bars(closes)
        findings = check_scale_shock(bars)
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.code, "SCALE_SHOCK")
        self.assertEqual(finding.severity, Severity.WARN)
        self.assertGreater(finding.detail["z_score"], 8.0)

    def test_scale_shock_skips_flat_history(self):
        # A perfectly flat window has sigma 0 — no scale, no verdict.
        # The stale check owns that case; the split detector owns the jump.
        bars = _bars([100.0] * 6 + [200.0])
        self.assertEqual(check_scale_shock(bars), [])


class SplitTests(unittest.TestCase):
    def test_split_detected_on_planted_pattern(self):
        # A 4:1 split with same-day drift: 100.8 -> 25.1 is ln(0.24901)
        # = -1.3904, within tolerance of ln 4 = 1.3863, and 7.9x volume.
        closes = [100.0, 100.5, 101.0, 100.8, 25.1, 25.4, 25.6, 25.5]
        volumes = [1000.0, 1100.0, 1050.0, 1080.0, 8500.0, 3000.0, 2600.0, 2500.0]
        bars = _bars(closes, volumes=volumes)
        findings = detect_splits(bars)
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.code, "SPLIT_SUSPECTED")
        self.assertEqual(finding.severity, Severity.LEAK)
        self.assertEqual(finding.detail["ratio"], 4)

    def test_noise_does_not_trip_the_detector(self):
        # A 60% crash (ln 0.4 = -0.916, between ln 2 and ln 3, matching
        # nothing) and a volume spike without a split-sized move.
        crash = _bars([100.0, 40.0, 39.5], volumes=[1000.0, 1200.0, 1100.0])
        self.assertEqual(detect_splits(crash), [])
        spike = _bars([100.0, 101.0], volumes=[1000.0, 5000.0])
        self.assertEqual(detect_splits(spike), [])

    def test_reverse_split_also_flags(self):
        # The abs() in the rule is deliberate: a 1:4 reverse split is
        # +ln 4 with the same volume signature.
        bars = _bars([25.0, 100.0], volumes=[1000.0, 4000.0])
        findings = detect_splits(bars)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].detail["ratio"], 4)


class ScanTests(unittest.TestCase):
    def test_scan_catches_negative_shift(self):
        findings = scan_source("out = df.shift(-1)\n")
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.code, "AST_NEG_SHIFT")
        self.assertEqual(finding.severity, Severity.WARN)
        self.assertEqual(finding.detail["rule"], "R1")
        self.assertIn("hint, not proof", finding.message)
        self.assertTrue(finding.where.startswith("line "))

    def test_scan_positive_shift_and_keyword_forms(self):
        self.assertEqual(scan_source("out = df.shift(1)\n"), [])
        self.assertEqual(scan_source("out = df.shift(periods=1)\n"), [])
        findings = scan_source("out = df.shift(periods=-2)\n")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].code, "AST_NEG_SHIFT")

    def test_scan_catches_tail_indexing(self):
        findings = scan_source("last = df.iloc[-1]\nalso = df.loc[-2]\n")
        self.assertEqual(len(findings), 2)
        self.assertEqual({f.code for f in findings}, {"AST_TAIL_INDEX"})
        findings = scan_source("tail = df.iloc[-3:, -1]\n")
        self.assertEqual(len(findings), 1)
        # positive indexes stay quiet
        self.assertEqual(scan_source("first = df.iloc[0]\nalso = df.loc[3]\n"), [])

    def test_scan_catches_future_names_in_subscripts(self):
        source = (
            "x = df[tomorrow]\n"
            "y = prices.loc[next_day]\n"
            "z = df['t_plus_1_close']\n"
        )
        findings = scan_source(source)
        self.assertEqual(len(findings), 3)
        self.assertEqual({f.code for f in findings}, {"AST_FUTURE_NAME"})
        # ordinary names, and future-flavored names NOT used as
        # subscripts, stay quiet
        quiet = scan_source("x = df[past]\nFOO = future_returns.mean()\n")
        self.assertEqual(quiet, [])


class RuntimeProbeTests(unittest.TestCase):
    def setUp(self):
        # Two-clock world: revenue known since t0-45d; a restatement
        # exists but the world only learns of it at t0 + 6 days.
        self.t0 = to_utc("2024-03-01T12:00:00+00:00")
        self.ledger = Ledger("fundamentals")
        self.ledger.append(
            "SYM2.revenue", 120.0,
            event_time=self.t0 - timedelta(days=90),
            knowledge_time=self.t0 - timedelta(days=45),
        )
        self.ledger.append(
            "SYM2.revenue", 84.0,
            event_time=self.t0 - timedelta(days=90),
            knowledge_time=self.t0 + timedelta(days=6),
        )
        self.clock = Clock(self.t0)

    def test_honest_strategy_passes(self):
        def honest(view):
            return view.get("SYM2.revenue")

        # At t0 and t0+1d the honest view shows rev0 both times (the
        # restatement is 5 days from being knowable) — same world, same
        # answer, probe passes.
        self.assertEqual(
            assert_no_future_access(honest, self.ledger, self.clock, steps=1),
            120.0,
        )

    def test_peeking_strategy_fails(self):
        ledger = self.ledger

        def peeker(view):
            # The classic: bypass the view, read the ledger relative to
            # "now + 5 days" — a restatement lands inside the probe window.
            return ledger.value_at("SYM2.revenue", view.as_of + timedelta(days=5))

        # At t0 the peek returns 120.0 (restatement not knowable at
        # t0+5); at t0+1d it returns 84.0 (knowable at t0+6).  The
        # difference is the confession.
        with self.assertRaises(LeakError):
            assert_no_future_access(peeker, self.ledger, self.clock, steps=1)

    def test_leak_trap_records_finding_and_returns_poisoned_result(self):
        ledger = self.ledger

        def peeker(view):
            return ledger.value_at("SYM2.revenue", view.as_of + timedelta(days=5))

        trap = LeakTrap(peeker, self.ledger, self.clock, steps=1)
        self.assertEqual(trap(), 120.0)  # the poisoned output still comes out
        self.assertEqual(len(trap.findings), 1)
        finding = trap.findings[0]
        self.assertEqual(finding.code, "RUNTIME_LEAK")
        self.assertEqual(finding.severity, Severity.LEAK)
        self.assertTrue(trap.has_leaks)
        self.assertEqual(trap.calls, [self.t0])

    def test_leak_trap_stays_quiet_on_honest(self):
        def honest(view):
            return view.get("SYM2.revenue")

        trap = LeakTrap(honest, self.ledger, self.clock, steps=1)
        self.assertEqual(trap(), 120.0)
        self.assertEqual(trap.findings, [])


class RegistryTests(unittest.TestCase):
    def test_run_all_aggregates_data_crimes(self):
        dates = _weekday_dates(10)
        # 4:1 split overnight from bar 4 to bar 5 (101.0 -> 25.2, split-day
        # volume 9x); a duplicated timestamp stacked on bars 7/8.
        closes = [100.0, 100.4, 100.2, 100.9, 101.0, 25.2, 25.4, 25.3, 25.6, 25.5]
        volumes = [1000.0, 1000.0, 1000.0, 1000.0, 1000.0, 9000.0, 4000.0, 2000.0, 2000.0, 2000.0]
        bars = _bars(closes, volumes=volumes, dates=dates)
        bars[8]["ts"] = bars[7]["ts"]  # duplicate stacked on top of the split
        report = run_all(bars)
        codes = {f.code for f in report.findings}
        self.assertIn("SPLIT_SUSPECTED", codes)
        self.assertIn("DUPLICATE_TS", codes)
        self.assertTrue(report.has_leaks)
        counts = report.counts()
        self.assertEqual(counts[Severity.LEAK], 2)
        self.assertEqual(counts[Severity.WARN], 0)  # clean otherwise

    def test_run_all_clean_and_with_lookahead_scan(self):
        bars = _bars(SAWTOOTH * 5)
        self.assertEqual(run_all(bars).findings, [])
        scanned = run_all(bars, with_lookahead_scan="pos = df.iloc[-1]\n")
        self.assertEqual(len(scanned.findings), 1)
        self.assertEqual(scanned.findings[0].code, "AST_TAIL_INDEX")

    def test_summarize_is_an_aligned_table_leaks_first(self):
        report = CheckReport()
        report.add(Finding("STALE_CLOSES", Severity.WARN, "m"))
        report.add(Finding("SPLIT_SUSPECTED", Severity.LEAK, "m"))
        report.add(Finding("SPLIT_SUSPECTED", Severity.LEAK, "m"))
        lines = summarize(report).splitlines()
        self.assertEqual(lines[0].split(), ["CODE", "SEVERITY", "COUNT"])
        self.assertEqual(lines[1].split(), ["SPLIT_SUSPECTED", "LEAK", "2"])
        self.assertEqual(lines[2].split(), ["STALE_CLOSES", "WARN", "1"])
        self.assertEqual(len({len(line) for line in lines}), 1, "rows must align")

    def test_summarize_empty_report(self):
        self.assertEqual(summarize(CheckReport()), "no findings")


if __name__ == "__main__":
    unittest.main()
