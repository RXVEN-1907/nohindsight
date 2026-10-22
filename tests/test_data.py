"""tests.test_data - module 1, the data department.

Everything here is offline and deterministic: the synthetic generators seed
their own randomness, the source parsers are exercised on inline fixtures,
and no test touches the network.  The planted-crime tests scan the ledgers
the way the checks module will - by reading entries, not by trusting the
generator's promises.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import unittest
from collections import Counter
from datetime import timedelta

from nohindsight.core import to_utc
from nohindsight.data import (
    Ledger,
    Universe,
    alfred_vintage_url,
    company_concept_url,
    facts_to_ledger,
    fred_series_url,
    ingest_csv,
    load_ledger,
    save_ledger,
    synthetic_fundamentals,
    synthetic_macro,
    synthetic_prices,
    synthetic_trades,
    vintages_to_ledger,
)


def _bars(ledger, key):
    """(event_time, value) pairs for key, ordered by event then knowledge time."""
    return [
        (e.event_time, e.value)
        for e in sorted(ledger.history(key), key=lambda e: (e.event_time, e.knowledge_time))
    ]


def _stalest_run(closes):
    """Longest run of exactly-equal consecutive closes."""
    best = run = 1
    for a, b in zip(closes, closes[1:]):
        run = run + 1 if b == a else 1
        best = max(best, run)
    return best


def _missing_weekdays(a, b):
    """Weekdays strictly between two datetime stamps."""
    d, n = a.date() + timedelta(days=1), 0
    while d < b.date():
        if d.weekday() < 5:
            n += 1
        d += timedelta(days=1)
    return n


class PersistedTests(unittest.TestCase):
    def _sample_ledger(self):
        led = Ledger("sample")
        led.append("A", {"close": 10.0}, "2024-01-02T20:00:00+00:00", "2024-01-02T20:00:00+00:00")
        led.append("A", {"close": 10.5}, "2024-01-03T20:00:00+00:00", "2024-01-03T20:00:00+00:00")
        # a correction: same event, published the next morning
        led.append("A", {"close": 10.2}, "2024-01-03T20:00:00+00:00", "2024-01-04T14:00:00+00:00")
        led.append("B", 42, "2024-01-02", "2024-01-05")
        return led

    def test_roundtrip_preserves_entries_and_revisions(self):
        led = self._sample_ledger()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sample.jsonl")
            self.assertEqual(save_ledger(led, path), 4)
            back = load_ledger(path, name="roundtrip")
        self.assertEqual(back.name, "roundtrip")
        self.assertEqual(len(back), 4)
        for a, b in zip(led.entries(), back.entries()):
            self.assertEqual(a.key, b.key)
            self.assertEqual(a.value, b.value)
            self.assertEqual(a.event_time, b.event_time)
            self.assertEqual(a.knowledge_time, b.knowledge_time)
            self.assertEqual(a.revision, b.revision)
        # the correction reconstructs: which number you see depends on when
        self.assertEqual(back.value_at("A", to_utc("2024-01-03T21:00:00+00:00")), {"close": 10.5})
        self.assertEqual(back.value_at("A", to_utc("2024-01-04T15:00:00+00:00")), {"close": 10.2})

    def test_save_writes_jsonl_atomically(self):
        led = self._sample_ledger()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sample.jsonl")
            save_ledger(led, path)
            self.assertFalse(os.path.exists(path + ".tmp"))  # replace left no debris
            with open(path, encoding="utf-8") as fh:
                lines = [json.loads(line) for line in fh if line.strip()]
        self.assertEqual(len(lines), 4)
        self.assertEqual(set(lines[0]), {"key", "value", "event_time", "knowledge_time"})

    def test_malformed_json_line_names_the_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.jsonl")
            good = json.dumps(
                {"key": "A", "value": 1.0, "event_time": "2024-01-02", "knowledge_time": "2024-01-02"}
            )
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(good + "\n")
                fh.write("{not json\n")
            with self.assertRaises(ValueError) as ctx:
                load_ledger(path)
        self.assertIn("line 2", str(ctx.exception))

    def test_non_object_line_names_the_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bad.jsonl")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("[1, 2, 3]\n")  # valid JSON, not an object
            with self.assertRaises(ValueError) as ctx:
                load_ledger(path)
        self.assertIn("line 1", str(ctx.exception))

    def test_empty_ledger_roundtrip(self):
        led = Ledger("empty")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "empty.jsonl")
            self.assertEqual(save_ledger(led, path), 0)
            back = load_ledger(path)
        self.assertEqual(len(back), 0)


class SyntheticPricesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ledger = synthetic_prices(n_days=240, seed=42)

    def test_shape_and_ohlc_integrity(self):
        led = self.ledger
        self.assertEqual(led.name, "prices")
        self.assertEqual(set(led.keys()), {f"SYM{i}" for i in range(1, 9)})
        for key in led.keys():
            bars = _bars(led, key)
            self.assertGreaterEqual(len(bars), 200)  # every symbol carried most days
            for ts, v in bars:
                self.assertEqual(set(v), {"open", "high", "low", "close", "volume"})
                self.assertGreater(v["low"], 0.0)
                self.assertGreaterEqual(v["high"], max(v["open"], v["close"]))
                self.assertLessEqual(v["low"], min(v["open"], v["close"]))
                self.assertGreaterEqual(v["volume"], 0.0)
                self.assertLess(ts.weekday(), 5)  # weekends never trade

    def test_planted_split_on_sym3(self):
        bars = _bars(self.ledger, "SYM3")
        hits = []
        for a, b in zip(bars, bars[1:]):
            log_ret = math.log(b[1]["close"] / a[1]["close"])
            vol_ratio = b[1]["volume"] / a[1]["volume"] if a[1]["volume"] else math.inf
            if abs(abs(log_ret) - math.log(4.0)) < 0.02 and vol_ratio > 3.0:
                hits.append(vol_ratio)
        self.assertEqual(len(hits), 1)  # exactly one 4:1 overnight with a volume spike
        self.assertGreaterEqual(hits[0], 5.0)

    def test_no_accidental_split_signature_elsewhere(self):
        for sym in ("SYM1", "SYM2", "SYM4", "SYM5", "SYM6", "SYM8"):
            bars = _bars(self.ledger, sym)
            for a, b in zip(bars, bars[1:]):
                log_ret = abs(math.log(b[1]["close"] / a[1]["close"]))
                self.assertLess(
                    log_ret, 0.4, f"unexpected overnight move on {sym} at {a[0]}"
                )

    def test_planted_gap_on_sym5(self):
        bars = _bars(self.ledger, "SYM5")
        worst = max(
            _missing_weekdays(a[0], b[0]) for a, b in zip(bars, bars[1:])
        )
        self.assertGreaterEqual(worst, 9)  # nine trading days: exchange closure
        # an honest symbol never misses a weekday
        bars1 = _bars(self.ledger, "SYM1")
        self.assertEqual(
            max(_missing_weekdays(a[0], b[0]) for a, b in zip(bars1, bars1[1:])), 0
        )

    def test_planted_stale_run_on_sym6(self):
        closes = [e[1]["close"] for e in _bars(self.ledger, "SYM6")]
        self.assertGreaterEqual(_stalest_run(closes), 5)
        for sym in ("SYM1", "SYM2"):
            closes = [e[1]["close"] for e in _bars(self.ledger, sym)]
            self.assertLess(_stalest_run(closes), 3)  # a walk never repeats a close

    def test_planted_duplicate_on_sym7(self):
        led = self.ledger
        for sym in led.keys():
            times = Counter(e.event_time for e in led.history(sym))
            dups = {t: c for t, c in times.items() if c > 1}
            if sym == "SYM7":
                self.assertEqual(len(dups), 1)
                ((ts, count),) = dups.items()
                self.assertEqual(count, 2)
                pair = sorted(
                    (e for e in led.history(sym) if e.event_time == ts),
                    key=lambda e: e.knowledge_time,
                )
                self.assertNotEqual(pair[0].value, pair[1].value)  # it is a correction
                self.assertGreater(pair[1].knowledge_time, pair[0].knowledge_time)
            else:
                self.assertEqual(dups, {})

    def test_deterministic_given_seed(self):
        def entries(led):
            return [(e.key, e.value, e.event_time, e.knowledge_time) for e in led.entries()]

        a = synthetic_prices(n_days=60, seed=123)
        b = synthetic_prices(n_days=60, seed=123)
        c = synthetic_prices(n_days=60, seed=124)
        self.assertEqual(entries(a), entries(b))
        self.assertNotEqual(entries(a), entries(c))


class FundamentalsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ledger = synthetic_fundamentals()

    def _restated_pair(self):
        h = self.ledger.history("SYM2.revenue")
        duplicated = [t for t, c in Counter(e.event_time for e in h).items() if c == 2]
        self.assertEqual(len(duplicated), 1)
        period = duplicated[0]
        pair = sorted((e for e in h if e.event_time == period), key=lambda e: e.knowledge_time)
        return pair[0], pair[1]

    def test_restatement_visible_in_history(self):
        led = self.ledger
        self.assertEqual(led.name, "fundamentals")
        h = led.history("SYM2.revenue")
        self.assertEqual(len(h), 9)  # 8 quarters + 1 restatement
        original, restated = self._restated_pair()
        self.assertAlmostEqual(restated.value, original.value * 0.7, delta=1e-3)
        self.assertEqual(
            restated.knowledge_time - original.knowledge_time, timedelta(days=21)
        )
        # it is the most recent quarter that got restated
        self.assertEqual(original.event_time, max(e.event_time for e in h))

    def test_honest_view_shows_original_until_restatement(self):
        led = self.ledger
        original, restated = self._restated_pair()
        just_after_original = original.knowledge_time + timedelta(seconds=1)
        self.assertEqual(led.value_at("SYM2.revenue", just_after_original), original.value)
        just_before = restated.knowledge_time - timedelta(seconds=1)
        self.assertEqual(led.value_at("SYM2.revenue", just_before), original.value)
        self.assertEqual(led.value_at("SYM2.revenue", restated.knowledge_time), restated.value)

    def test_other_symbols_have_no_restatement(self):
        led = self.ledger
        for i in (1, 3, 4, 5, 6, 7, 8):
            h = led.history(f"SYM{i}.revenue")
            self.assertEqual(len(h), 8)
            self.assertEqual(len({e.event_time for e in h}), 8)  # one entry per quarter

    def test_filing_lag_is_quarterly_plus_weeks(self):
        for e in self.ledger.history("SYM1.revenue"):
            lag = e.knowledge_time - e.event_time
            self.assertGreaterEqual(lag, timedelta(days=30))
            self.assertLessEqual(lag, timedelta(days=60))


class MacroTests(unittest.TestCase):
    def test_two_vintages_per_month(self):
        led = synthetic_macro()
        self.assertEqual(led.name, "macro")
        h = led.history("UNRATE")
        self.assertEqual(len(h), 6 * 12 * 2)
        by_period = {}
        for e in h:
            by_period.setdefault(e.event_time, []).append(e)
        self.assertEqual(len(by_period), 72)
        for period, vintages in by_period.items():
            vintages.sort(key=lambda e: e.knowledge_time)
            first, revised = vintages
            self.assertEqual(first.knowledge_time - period, timedelta(days=7))
            self.assertEqual(revised.knowledge_time - period, timedelta(days=38))
            self.assertTrue(0.1 <= abs(revised.value - first.value) <= 0.7)

    def test_macro_deterministic_given_seed(self):
        def entries(led):
            return [(e.value, e.event_time, e.knowledge_time) for e in led.entries()]

        a = synthetic_macro(years=2, seed=11)
        b = synthetic_macro(years=2, seed=11)
        c = synthetic_macro(years=2, seed=12)
        self.assertEqual(entries(a), entries(b))
        self.assertNotEqual(entries(a), entries(c))


class TradesTests(unittest.TestCase):
    def test_shape(self):
        for t in synthetic_trades(n=200):
            self.assertEqual(set(t), {"ts", "symbol", "side", "price", "qty"})
            self.assertIn(t["side"], ("buy", "sell"))
            self.assertIn(t["symbol"], {f"SYM{i}" for i in range(1, 9)})
            to_utc(t["ts"])  # parses
            self.assertGreater(t["price"], 0.0)
            self.assertGreater(t["qty"], 0)

    def test_block_trades_exist_and_seed_is_deterministic(self):
        a = synthetic_trades(n=400, seed=3)
        b = synthetic_trades(n=400, seed=3)
        self.assertEqual(a, b)
        big = [t for t in a if t["qty"] >= 2000]  # the impact-relevant tail
        self.assertGreater(len(big), 5)


class TabularTests(unittest.TestCase):
    CSV = (
        "sym,ts,filed,close,volume\n"
        "SYM1,2024-01-02T20:00:00+00:00,2024-01-03T14:00:00+00:00,101.5,12000\n"
        "SYM1,2024-01-03T20:00:00+00:00,2024-01-04T14:00:00+00:00,102.25,13000\n"
    )

    def _write(self, tmp, text, name="in.csv"):
        path = os.path.join(tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_value_col_coerces_floats(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, self.CSV)
            led = ingest_csv(path, "sym", "ts", "filed", value_col="close")
        self.assertEqual(led.name, "ingested")
        self.assertEqual(len(led), 2)
        h = led.history("SYM1")
        self.assertEqual(h[0].value, 101.5)
        self.assertIsInstance(h[0].value, float)
        self.assertEqual(h[1].value, 102.25)

    def test_value_cols_build_dict_and_strings_survive(self):
        csv_text = (
            "key,event,knowledge,price,note\n"
            "X,2024-01-02,2024-01-02,12.5,heavy\n"
            "X,2024-01-03,2024-01-03,13.0,light\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, csv_text)
            led = ingest_csv(path, "key", "event", "knowledge", value_cols=["price", "note"])
            bare = ingest_csv(path, "key", "event", "knowledge")  # all remaining columns
        self.assertEqual(led.value_at("X"), {"price": 13.0, "note": "light"})
        self.assertEqual(bare.value_at("X"), {"price": 13.0, "note": "light"})

    def test_rejects_knowledge_before_event_naming_the_row(self):
        csv_text = (
            "key,event,knowledge,px\n"
            "A,2024-01-02,2024-01-02,1.0\n"
            "A,2024-01-05,2024-01-04,2.0\n"  # filed before it happened
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, csv_text)
            with self.assertRaises(ValueError) as ctx:
                ingest_csv(path, "key", "event", "knowledge", value_col="px")
        self.assertIn("line 3", str(ctx.exception))
        self.assertIn("precedes", str(ctx.exception))

    def test_missing_column_names_the_problem(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, self.CSV)
            with self.assertRaises(ValueError):
                ingest_csv(path, "sym", "ts", "nope")

    def test_csv_ledger_survives_persisted_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, self.CSV)
            led = ingest_csv(path, "sym", "ts", "filed", value_cols=["close", "volume"])
            p2 = os.path.join(tmp, "out.jsonl")
            save_ledger(led, p2)
            back = load_ledger(p2, name="back")
        for a, b in zip(led.entries(), back.entries()):
            self.assertEqual((a.key, a.value, a.event_time, a.knowledge_time),
                             (b.key, b.value, b.event_time, b.knowledge_time))


class UniverseTests(unittest.TestCase):
    def _universe(self):
        u = Universe()
        u.add("AAA", "2020-01-01")
        u.add("BBB", "2020-06-01", "2022-06-01")
        u.add("CCC", "2021-01-01", "2021-06-01")
        u.add("DDD", "2023-01-01")
        return u

    def test_members_at(self):
        u = self._universe()
        self.assertEqual(u.members_at("2020-01-01"), ["AAA"])
        self.assertEqual(u.members_at("2021-12-31"), ["AAA", "BBB"])
        self.assertEqual(u.members_at("2023-01-02"), ["AAA", "DDD"])
        # listing day inclusive, delisting day exclusive
        self.assertIn("BBB", u.members_at("2022-05-31"))
        self.assertNotIn("BBB", u.members_at("2022-06-01"))

    def test_survivorship_counts(self):
        u = self._universe()
        self.assertEqual(
            u.survivorship_report("2023-01-01"),
            {"current": 2, "ever_listed": 4, "ghosts": 2},
        )
        self.assertEqual(
            u.survivorship_report("2019-12-31"),
            {"current": 0, "ever_listed": 0, "ghosts": 0},
        )

    def test_delisted_between(self):
        u = self._universe()
        self.assertEqual(u.delisted_between("2021-01-01", "2022-12-31"), ["BBB", "CCC"])
        self.assertEqual(u.delisted_between("2023-01-01", "2024-01-01"), [])

    def test_readd_is_rejected(self):
        u = self._universe()
        with self.assertRaises(ValueError):
            u.add("AAA", "2024-01-01")


class EdgarTests(unittest.TestCase):
    def test_company_concept_url_exact(self):
        self.assertEqual(
            company_concept_url(320193, "us-gaap", "Revenues", "USD"),
            "https://data.sec.gov/api/xbrl/companyconcept/CIK0000320193/us-gaap/Revenues/USD.json",
        )

    def test_facts_to_ledger_two_filings_same_period(self):
        payload = {
            "cik": 320193,
            "entityName": "Apple Inc.",
            "units": {
                "USD": [
                    {"end": "2023-09-30", "filed": "2023-11-03", "val": 383285000000,
                     "fy": 2023, "fp": "FY"},
                    {"end": "2023-09-30", "filed": "2024-01-15", "val": 383290000000,
                     "fy": 2023, "fp": "FY"},  # same period, filed again
                    {"end": "2023-12-31", "filed": "2024-02-01", "val": 119575000000,
                     "fy": 2024, "fp": "Q1"},
                ]
            },
        }
        led = facts_to_ledger(payload, "Revenues")
        h = led.history("320193.Revenues")
        self.assertEqual(len(h), 3)
        same_period = [e for e in h if e.event_time == to_utc("2023-09-30")]
        self.assertEqual([e.revision for e in same_period], [0, 1])  # 2 filings, 2 revisions
        # the honest view flips only when the second filing became public
        self.assertEqual(led.value_at("320193.Revenues", to_utc("2023-12-01")), 383285000000)
        self.assertEqual(led.value_at("320193.Revenues", to_utc("2024-01-20")), 383290000000)

    def test_facts_to_ledger_skips_incomplete_facts(self):
        payload = {
            "cik": 1,
            "units": {
                "USD": [
                    {"end": "2023-09-30", "filed": "2023-11-03"},  # no val
                    {"end": "2023-09-30", "filed": "2023-11-03", "val": 5.0},
                ]
            },
        }
        self.assertEqual(len(facts_to_ledger(payload, "Revenues")), 1)


class FredTests(unittest.TestCase):
    def test_url_builders_exact(self):
        self.assertEqual(
            alfred_vintage_url("UNRATE", "2020-01-01"),
            "https://alfred.stlouisfed.org/graph/alfred_graph.csv?id=UNRATE&cosd=2020-01-01",
        )
        self.assertEqual(
            fred_series_url("UNRATE", "abcd1234"),
            "https://api.stlouisfed.org/series/observations"
            "?series_id=UNRATE&file_type=json&api_key=abcd1234",
        )

    def test_vintages_to_ledger_fixture(self):
        csv_text = (
            "realtime_start,realtime_end,date,UNRATE\n"
            "2020-03-13,2020-04-13,2020-03-01,3.5\n"
            "2020-04-14,2020-05-14,2020-03-01,4.4\n"
            "2020-04-13,2020-05-13,2020-04-01,14.7\n"
            "2020-05-13,2020-06-13,2020-05-01,.\n"
        )
        led = vintages_to_ledger(csv_text, "UNRATE")
        self.assertEqual(led.name, "fred.UNRATE")
        h = led.history("UNRATE")
        self.assertEqual(len(h), 3)  # the "." row carries no datum
        march = sorted(
            (e for e in h if e.event_time == to_utc("2020-03-01")),
            key=lambda e: e.knowledge_time,
        )
        self.assertEqual([e.value for e in march], [3.5, 4.4])  # estimate, then revision
        # the honest read: on April 1 only the first vintage existed
        self.assertEqual(led.value_at("UNRATE", to_utc("2020-04-01")), 3.5)
        self.assertEqual(led.value_at("UNRATE", to_utc("2020-04-20")), 4.4)


if __name__ == "__main__":
    unittest.main()
