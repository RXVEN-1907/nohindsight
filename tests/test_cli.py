"""tests.test_cli — the integration seam: report, cli, demo.

Everything here runs the REAL pipeline (synthetic data -> checks -> replay ->
audit -> report) and asserts on what lands on disk and on stdout, because
the integration module's product is exactly that: files and exit codes.
Deterministic throughout — the demo's report is byte-for-byte reproducible.
"""

from __future__ import annotations

import contextlib
import csv
import io
import os
import random
import re
import tempfile
import unittest
from pathlib import Path

from nohindsight import __version__
from nohindsight.cli import main

_VERDICT_WORD = re.compile(r"\b(REAL|SUSPICIOUS|FANTASY)\b")


def _run(argv: list[str]) -> tuple[int, str]:
    """main(argv) with stdout captured; returns (exit_code, stdout)."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main(argv)
    return code, buffer.getvalue()


def _write_returns_csv(path: Path, rows: list[float]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["return"])
        for r in rows:
            writer.writerow([f"{r:.6f}"])


def _write_bars_csv(path: Path, bars: list[dict]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["symbol", "ts", "open", "high", "low", "close", "volume"])
        for bar in bars:
            writer.writerow(
                [bar["symbol"], bar["ts"], bar["open"], bar["high"],
                 bar["low"], bar["close"], bar["volume"]]
            )


def _clean_bars(n: int = 30) -> list[dict]:
    """Thirty healthy bars for one symbol, weekdays from 2024-01-02."""
    from datetime import date, timedelta

    bars: list[dict] = []
    d = date(2024, 1, 2)
    price = 100.0
    rng = random.Random(17)
    while len(bars) < n:
        if d.weekday() < 5:
            open_ = price * (1 + rng.gauss(0, 0.002))
            close = price * (1 + rng.gauss(0, 0.01))
            high = max(open_, close) * 1.005
            low = min(open_, close) * 0.995
            bars.append(
                {
                    "symbol": "GOOD",
                    "ts": f"{d.isoformat()}T20:00:00+00:00",
                    "open": round(open_, 2),
                    "high": round(high, 2),
                    "low": round(low, 2),
                    "close": round(close, 2),
                    "volume": 1_000_000.0,
                }
            )
            price = close
        d += timedelta(days=1)
    return bars


class TestVersion(unittest.TestCase):
    def test_version_prints_version_and_exits_zero(self):
        code, out = _run(["version"])
        self.assertEqual(code, 0)
        self.assertIn("nohindsight", out)
        self.assertIn(__version__, out)


class TestDemo(unittest.TestCase):
    def test_demo_completes_in_temp_dir_and_writes_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, out = _run(["demo", "--out", tmp])
            self.assertEqual(code, 0)
            self.assertIn("The naive strategy made more money", out)
            report = Path(tmp, "report.html").read_text(encoding="utf-8")
        # the report names the library and carries at least one verdict
        self.assertIn("nohindsight", report)
        self.assertRegex(report, _VERDICT_WORD)
        # and the findings table shows both departments at work:
        # the planted split (data police) and the restatement (leak police)
        self.assertIn("SPLIT_SUSPECTED", report)
        self.assertIn("RESTATEMENT_LEAK", report)

    def test_demo_report_is_reproducible_byte_for_byte(self):
        """Same seeds, same bytes — a report that cannot be reproduced is a
        story, not a test."""
        with tempfile.TemporaryDirectory() as t1, tempfile.TemporaryDirectory() as t2:
            _run(["demo", "--out", t1])
            _run(["demo", "--out", t2])
            a = Path(t1, "report.html").read_bytes()
            b = Path(t2, "report.html").read_bytes()
        self.assertEqual(a, b)

    def test_demo_naive_sharpe_beats_honest(self):
        """The demo's whole point, asserted: the leak looks BETTER on
        returns — which is exactly why the police department exists."""
        from nohindsight import demo

        result = demo.run(tempfile.mkdtemp(), verbose=False)
        self.assertGreater(
            result["naive_verdict"].numbers["sharpe"],
            result["honest_verdict"].numbers["sharpe"],
        )
        # the honest engine never tripped the kill switch — the divergence
        # in the story is the restatement, not the risk guard
        rules = {r.rule for r in result["honest_refusals"]}
        self.assertNotIn("kill_switch", rules)


class TestAudit(unittest.TestCase):
    def test_audit_on_drifty_csv_returns_real(self):
        rng = random.Random(5)
        rows = [0.002 + rng.gauss(0.0, 0.01) for _ in range(400)]
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp, "drifty.csv")
            _write_returns_csv(csv_path, rows)
            code, out = _run(["audit", str(csv_path)])
            self.assertEqual(code, 0)
            self.assertRegex(out, r"VERDICT:\s+REAL")
            report = Path(tmp, "drifty.report.html").read_text(encoding="utf-8")
        self.assertRegex(report, _VERDICT_WORD)
        self.assertIn("nohindsight", report)

    def test_audit_demo_flag_writes_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "audit-demo.html")
            code, out_text = _run(["audit", "--demo", "--out", out])
            self.assertEqual(code, 0)
            self.assertRegex(out_text, r"VERDICT:\s+REAL")
            self.assertTrue(Path(out).is_file())

    def test_audit_rejects_unparseable_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp, "bad.csv")
            csv_path.write_text("return\nnot-a-number\n0.01\n", encoding="utf-8")
            code, out = _run(["audit", str(csv_path)])
            self.assertEqual(code, 2)
            self.assertIn("error:", out)


class TestCheck(unittest.TestCase):
    def test_check_clean_csv_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp, "clean.csv")
            _write_bars_csv(csv_path, _clean_bars())
            code, out = _run(["check", str(csv_path)])
            self.assertEqual(code, 0)
            self.assertIn("no findings", out)
            report = Path(tmp, "clean.report.html").read_text(encoding="utf-8")
        self.assertIn("nohindsight", report)

    def test_check_broken_csv_exits_nonzero_and_reports_leaks(self):
        bars = _clean_bars()
        # plant a duplicated timestamp and an impossible bar
        bars.append(dict(bars[10]))  # same instant twice -> DUPLICATE_TS (LEAK)
        bars[5] = {**bars[5], "high": 1.0, "low": 0.5}  # high < open/close -> OHLC_INVALID (LEAK)
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp, "broken.csv")
            _write_bars_csv(csv_path, bars)
            code, out = _run(["check", str(csv_path)])
            self.assertEqual(code, 1)  # leaks found: nonzero by design
            self.assertRegex(out, r"DUPLICATE_TS")
            self.assertRegex(out, r"OHLC_INVALID")
            self.assertRegex(out, r"\b[1-9]\d* leaks")
            report = Path(tmp, "broken.report.html").read_text(encoding="utf-8")
        self.assertIn("LEAK", report)

    def test_check_missing_file_is_bad_input(self):
        code, out = _run(["check", "/nonexistent/nohindsight-bars.csv"])
        self.assertEqual(code, 2)
        self.assertIn("error:", out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
