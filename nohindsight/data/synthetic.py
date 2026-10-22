"""nohindsight.data.synthetic - a small world with crimes planted in it.

Everything here is deterministic given its seed (one ``random.Random`` per
call, draws in a fixed, documented order), so the demo tells the same story
every run and the tests can assert on exact numbers.

The prices generator plants four data crimes, one symbol each, and the
fundamentals generator plants a restatement.  These are the module's
contract with the checks package and the demo - the detectors are supposed
to find them, so the numbers are planted exactly, not approximately:

    SYM3  split        one overnight close->close drop of exactly 4:1
                       (log return = -ln 4) with a 5x volume spike;
                       history is left raw - no back-adjustment, which is
                       precisely the crime a chart vendor would have hidden
    SYM5  gap          nine consecutive trading days missing (exchange
                       closure); the walk simply pauses and resumes where
                       it left off, like a real tape
    SYM6  stale bars   five consecutive flat bars at the last honest close,
                       volume zero - a halted symbol or a dead feed
    SYM7  duplicate    one trading day appended twice; the second entry is
                       the correction, knowable the next weekday morning
    SYM2  restatement  (fundamentals) the most recent quarter re-filed
                       21 days after the original, 30% lower

Bars are stamped 20:00 UTC - 4pm US Eastern, the close the bar summarizes
- with event_time == knowledge_time: the stated simplification is that a
bar is knowable at its own close.  The crimes need roughly n_days >= 12 to
fit; below that each block shrinks to what the timeline allows.
"""

from __future__ import annotations

import calendar
import math
import random
from datetime import date, datetime, timedelta
from typing import Any

from ..core import Ledger, UTC, to_utc

__all__ = [
    "GAP_DAYS",
    "STALE_DAYS",
    "SPLIT_RATIO",
    "SPLIT_VOL_MULT",
    "synthetic_fundamentals",
    "synthetic_macro",
    "synthetic_prices",
    "synthetic_trades",
]

# Planted-crime constants: the demo and the checks module depend on these
# magnitudes, so they live here, named, rather than as magic numbers.
SPLIT_RATIO = 4.0  # SYM3's 4:1 split - overnight close drops by exactly this
SPLIT_VOL_MULT = 5.0  # split-day volume spike (> checks' vol_mult default of 3.0)
GAP_DAYS = 9  # SYM5's exchange closure, in trading days
STALE_DAYS = 5  # SYM6's dead-feed run of identical closes (> checks' run default of 4)
DUP_CORRECTION_FACTOR = 1.003  # SYM7's correction: close nudged +30bps, volume +10%

_SYMBOLS = ("SYM1", "SYM2", "SYM3", "SYM4", "SYM5", "SYM6", "SYM7", "SYM8")

# Fixed calendar anchors - never datetime.now(); a generator that depends on
# the wall clock is not reproducible.
_FUNDAMENTALS_LAST_QUARTER_END = date(2023, 12, 31)
_MACRO_LAST_MONTH_END = date(2023, 12, 31)
_TRADES_EPOCH = datetime(2024, 1, 2, tzinfo=UTC)  # same opening day as the prices


def _trading_day_stamps(start: str, n: int) -> list[datetime]:
    """`n` weekday stamps at 20:00 UTC, first on or after `start`."""
    d = to_utc(start).date()
    out: list[datetime] = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(datetime(d.year, d.month, d.day, 20, tzinfo=UTC))
        d += timedelta(days=1)
    return out


def _next_weekday_morning(ts: datetime) -> datetime:
    """14:00 UTC on the next weekday after ts - when a correction lands."""
    d = ts.date() + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return datetime(d.year, d.month, d.day, 14, tzinfo=UTC)


def _crime_layout(n: int) -> dict[str, Any]:
    """Where each crime lives, clamped so small n_days still works.

    Day 0 stays clean: every crime needs at least one honest bar before it
    as a witness.
    """
    split_idx = max(1, n // 2) if n >= 2 else None
    gap_start = min(max(1, (3 * n) // 5), max(1, n - 1 - GAP_DAYS))
    gap_len = max(0, min(GAP_DAYS, n - 1 - gap_start))
    stale_start = min(max(1, n // 3), max(1, n - STALE_DAYS))
    stale_len = max(0, min(STALE_DAYS, n - stale_start))
    dup_idx = min(max(1, (2 * n) // 3), n - 1) if n >= 2 else None
    return {
        "split_idx": split_idx,
        "gap": (gap_start, gap_len),
        "stale": (stale_start, stale_len),
        "dup_idx": dup_idx,
    }


def _normal_bar(
    prev_close: float,
    volume_base: float,
    drift: float,
    vol: float,
    rng: random.Random,
) -> dict[str, float]:
    """One honest daily bar: GBM close, small gap open, bracketing high/low."""
    open_ = prev_close * (1.0 + rng.gauss(0.0, 0.3 * vol))
    close = prev_close * math.exp(drift + rng.gauss(0.0, vol))
    high = max(open_, close) * (1.0 + abs(rng.gauss(0.0, 0.5 * vol)))
    low = min(open_, close) * (1.0 - abs(rng.gauss(0.0, 0.5 * vol)))
    volume = volume_base * math.exp(rng.gauss(0.0, 0.35))
    return {"open": open_, "high": high, "low": low, "close": close, "volume": volume}


def synthetic_prices(n_days: int = 750, seed: int = 42, start: str = "2024-01-02") -> Ledger:
    """Daily OHLCV bars for SYM1..SYM8, with four crimes planted.

    Keys are the symbols; values are plain dicts
    ``{"open", "high", "low", "close", "volume"}`` of floats - the trading
    day itself is the entry's event_time.  Deterministic given seed; RNG
    discipline: per-symbol (start, drift, vol, volume base) drawn first in
    symbol order, then bar-by-bar noise walking dates outer, symbols inner.

    The planted crimes are documented at module level and must not be
    softened: the split detector, gap/stale/duplicate checks and the demo
    all depend on finding them.
    """
    ledger = Ledger("prices")
    if n_days < 1:
        return ledger
    rng = random.Random(seed)
    layout = _crime_layout(n_days)
    stamps = _trading_day_stamps(start, n_days)

    cfg: dict[str, dict[str, float]] = {}
    for sym in _SYMBOLS:
        cfg[sym] = {
            "start": rng.uniform(15.0, 140.0),
            "drift": rng.uniform(-4e-4, 8e-4),
            "vol": rng.uniform(0.008, 0.028),
            "volume_base": rng.uniform(4e5, 5e6),
        }

    gap_start, gap_len = layout["gap"]
    stale_start, stale_len = layout["stale"]
    prev: dict[str, tuple[float, float]] = {}  # symbol -> (close, volume)

    for i, ts in enumerate(stamps):
        for sym in _SYMBOLS:
            c = cfg[sym]
            if sym == "SYM5" and gap_start <= i < gap_start + gap_len:
                continue  # exchange closed: no bar, the walk pauses

            close, volume = prev.get(sym, (c["start"], c["volume_base"]))
            if sym == "SYM6" and stale_start <= i < stale_start + stale_len:
                bar = {  # halted: frozen at the last honest close, no volume
                    "open": close,
                    "high": close,
                    "low": close,
                    "close": close,
                    "volume": 0.0,
                }
            elif sym == "SYM3" and layout["split_idx"] == i:
                # The crime: a raw, unadjusted 4:1 split.  The close drops
                # by exactly SPLIT_RATIO overnight on 5x volume; earlier
                # bars stay at their raw (pre-split) levels.
                volume = volume * SPLIT_VOL_MULT
                close = close / SPLIT_RATIO
                open_ = close * (1.0 + rng.gauss(0.0, 0.002))
                high = max(open_, close) * (1.0 + abs(rng.gauss(0.0, 0.002)))
                low = min(open_, close) * (1.0 - abs(rng.gauss(0.0, 0.002)))
                bar = {"open": open_, "high": high, "low": low, "close": close, "volume": volume}
            else:
                bar = _normal_bar(close, c["volume_base"], c["drift"], c["vol"], rng)

            if sym == "SYM7" and layout["dup_idx"] == i:
                correction = dict(bar)
                correction["close"] = bar["close"] * DUP_CORRECTION_FACTOR
                correction["high"] = max(bar["high"], correction["close"])
                correction["low"] = min(bar["low"], correction["close"])
                correction["volume"] = bar["volume"] * 1.1
                ledger.append(sym, correction, ts, _next_weekday_morning(ts))

            ledger.append(sym, bar, ts, ts)
            prev[sym] = (bar["close"], bar["volume"])
    return ledger


def _quarter_ends(count: int) -> list[date]:
    """The `count` calendar-quarter ends ending 2023-12-31, ascending."""
    out: list[date] = []
    y, q = _FUNDAMENTALS_LAST_QUARTER_END.year, 4
    for _ in range(count):
        month = 3 * q
        out.append(date(y, month, calendar.monthrange(y, month)[1]))
        q -= 1
        if q == 0:
            q, y = 4, y - 1
    return list(reversed(out))


def synthetic_fundamentals(symbols: int = 8, quarters: int = 8, seed: int = 7) -> Ledger:
    """Quarterly revenue in millions as reported, with one restatement.

    Key model: one key per symbol, ``"<SYM>.revenue"`` - each quarter
    appends a new revision, because that is how a "current fundamentals"
    feed actually behaves: the latest reported number moves forward each
    quarter, and which number you were allowed to see is a function of when
    you looked.  event_time = quarter end; knowledge_time = quarter end
    plus a drawn filing lag of 38-52 days (~45, per-symbol per-quarter).

    The crime: SYM2's most recent quarter gets restated - a further
    revision exactly 21 days after the original filing, value 30% lower.
    Until that 21-day mark the honest view still shows the original number;
    the naive view (reading today's ledger) never saw the original at all.
    Deterministic given seed.  Quarters end 2022-03-31 .. 2023-12-31.
    """
    ledger = Ledger("fundamentals")
    if symbols < 1 or quarters < 1:
        return ledger
    rng = random.Random(seed)
    ends = _quarter_ends(quarters)

    params: dict[str, tuple[float, float]] = {}
    for i in range(symbols):
        params[f"SYM{i + 1}"] = (rng.uniform(40.0, 400.0), rng.uniform(0.98, 1.09))
    level = {sym: base for sym, (base, _) in params.items()}

    last_end = ends[-1]
    sym2_original: tuple[float, datetime] | None = None
    for qe in ends:
        et = datetime(qe.year, qe.month, qe.day, tzinfo=UTC)
        for sym, (_, growth) in params.items():
            value = round(level[sym] * (1.0 + rng.gauss(0.0, 0.02)), 3)
            kt = et + timedelta(days=rng.uniform(38.0, 52.0))
            ledger.append(f"{sym}.revenue", value, et, kt)
            level[sym] = level[sym] * growth
            if sym == "SYM2" and qe == last_end:
                sym2_original = (value, kt)

    if sym2_original is not None and symbols >= 2:
        value, kt = sym2_original
        ledger.append(  # the restatement: same period, later knowledge, lower number
            "SYM2.revenue",
            round(value * 0.7, 3),
            datetime(last_end.year, last_end.month, last_end.day, tzinfo=UTC),
            kt + timedelta(days=21),
        )
    return ledger


def _month_ends(count: int) -> list[date]:
    """The `count` month ends ending 2023-12-31, ascending."""
    out: list[date] = []
    y, m = _MACRO_LAST_MONTH_END.year, _MACRO_LAST_MONTH_END.month
    for _ in range(count):
        out.append(date(y, m, calendar.monthrange(y, m)[1]))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def synthetic_macro(series: str = "UNRATE", years: int = 6, seed: int = 11) -> Ledger:
    """Monthly macro values with two vintages per month.

    Key = the series id.  event_time = month end (the period the number
    describes).  Two vintages per month: the first estimate is knowable at
    month end + 7 days, the revised estimate at + 38 days, revisions
    landing between 0.2 and 0.6 points in either direction.  A strategy
    that reads today's revised series for a past month is reading a number
    nobody in that month had.  Deterministic given seed; months end
    2018-01-31 .. 2023-12-31 for the default years=6.
    """
    ledger = Ledger("macro")
    if years < 1:
        return ledger
    rng = random.Random(seed)
    level = rng.uniform(3.5, 6.5)
    for me in _month_ends(years * 12):
        et = datetime(me.year, me.month, me.day, tzinfo=UTC)
        level = min(15.0, max(2.0, level + rng.gauss(0.0, 0.25)))
        first = round(level, 1)
        sign = 1.0 if rng.random() < 0.5 else -1.0
        revised = round(first + sign * rng.uniform(0.2, 0.6), 1)
        ledger.append(series, first, et, et + timedelta(days=7))
        ledger.append(series, revised, et, et + timedelta(days=38))
    return ledger


def synthetic_trades(n: int = 400, seed: int = 3) -> list[dict]:
    """`n` trade records for the costs module to price out.

    ``{"ts", "symbol", "side", "price", "qty"}`` - ts an ISO string inside
    2024-Q1, side "buy"/"sell".  qty is log-normal with a deliberate fat
    tail: mostly a few hundred shares, ~5% block trades large enough that
    impact (not fees) dominates.  Deterministic given seed.
    """
    rng = random.Random(seed)
    out: list[dict] = []
    for _ in range(n):
        sym = _SYMBOLS[rng.randrange(len(_SYMBOLS))]
        ts = _TRADES_EPOCH + timedelta(
            days=rng.randrange(90), seconds=rng.randrange(86400)
        )
        side = "buy" if rng.random() < 0.5 else "sell"
        base = 15.0 + 12.5 * (_SYMBOLS.index(sym) + 1)
        price = round(base * (1.0 + rng.gauss(0.0, 0.02)), 2)
        if rng.random() < 0.05:
            qty = int(round(math.exp(rng.gauss(9.0, 0.4))))  # block trade
        else:
            qty = int(round(math.exp(rng.gauss(6.2, 0.5))))
        out.append(
            {"ts": ts.isoformat(), "symbol": sym, "side": side, "price": price, "qty": qty}
        )
    return out
