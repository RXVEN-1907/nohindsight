"""nohindsight.checks.quality - bar-level police.

Conventions every check in here shares:

  * A *bar* is a plain dict carrying at least {"open", "high", "low",
    "close"} plus "ts" (an ISO-8601 string); "volume", "bid", "ask" are
    optional and only the checks that need them look at them.
  * Bars arrive sorted ascending by ts.  That ordering is the caller's
    contract (registry, demo); checks are pure functions and never
    reorder, copy, or mutate.
  * Every check returns list[Finding] and takes no clock: these are
    properties of the data itself, not of when you looked at it.
  * One finding per offense site - one bad bar, one duplicated instant,
    one stale run, one gap - so a report can point at the wound.
"""

from __future__ import annotations

import math
import statistics
from datetime import date, datetime, timedelta

from ..core import to_utc
from .base import Finding, Severity

__all__ = [
    "check_ohlc",
    "check_duplicates",
    "check_gaps",
    "check_stale",
    "check_crossed",
    "check_scale_shock",
]

_OHLC_FIELDS = ("open", "high", "low", "close")

# Scale-shock internals.  Deliberately not parameters: the public
# signature is pinned, and these two numbers are judgments, not knobs.
_SHOCK_WINDOW = 30   # trailing daily returns that count as "recent"
_SHOCK_MIN_OBS = 8   # fewer than this and a sigma is a rumor, not a scale


def _finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


def _usable_close(value: object) -> float | None:
    """Positive finite close, else None - log returns need a real price."""
    if isinstance(value, (int, float)) and math.isfinite(value) and value > 0:
        return float(value)
    return None


def check_ohlc(bars: list[dict]) -> list[Finding]:
    """Geometry police: high >= max(open, close), low <= min(open, close),
    low > 0, all four fields finite numbers.

    Violations are LEAK: data this broken poisons anything built on it.
    At most one finding per bar (unreadable fields short-circuit the
    geometry test - there is nothing left to judge).
    """
    findings: list[Finding] = []
    for i, bar in enumerate(bars):
        ts = bar.get("ts")
        values: dict[str, float] = {}
        unreadable: list[str] = []
        for name in _OHLC_FIELDS:
            value = bar.get(name)
            if _finite_number(value):
                values[name] = float(value)
            else:
                unreadable.append(f"{name}={value!r} is not a finite number")
        if unreadable:
            findings.append(Finding(
                code="OHLC_INVALID",
                severity=Severity.LEAK,
                message=f"bar {i} ({ts}): " + "; ".join(unreadable),
                where=f"bars[{i}]",
                detail={"ts": ts, "bar_index": i, "problems": unreadable},
            ))
            continue
        o, h, low, c = values["open"], values["high"], values["low"], values["close"]
        problems: list[str] = []
        if low <= 0:
            problems.append(f"low {low} <= 0")
        if h < max(o, c):
            problems.append(f"high {h} < max(open, close) {max(o, c)}")
        if low > min(o, c):
            problems.append(f"low {low} > min(open, close) {min(o, c)}")
        if problems:
            findings.append(Finding(
                code="OHLC_INVALID",
                severity=Severity.LEAK,
                message=f"bar {i} ({ts}): " + "; ".join(problems),
                where=f"bars[{i}]",
                detail={"ts": ts, "bar_index": i, "problems": problems},
            ))
    return findings


def check_duplicates(bars: list[dict]) -> list[Finding]:
    """The same instant twice -> LEAK.

    A duplicated timestamp is either a correction appended without
    revisioning or a feed hiccup; either way the series no longer says
    one thing per instant.  Timestamps are compared as instants via
    to_utc, so "...Z" and "...+00:00" spellings of the same moment
    collide.  One finding per duplicated instant (not per pair).  Bars
    whose ts can't be parsed are skipped - ingest owns that failure.
    """
    first_seen: dict[datetime, int] = {}
    dupes: dict[datetime, list[int]] = {}
    for i, bar in enumerate(bars):
        try:
            instant = to_utc(bar.get("ts"))
        except (TypeError, ValueError):
            continue
        if instant in first_seen:
            dupes.setdefault(instant, [first_seen[instant]]).append(i)
        else:
            first_seen[instant] = i
    findings: list[Finding] = []
    for instant, indices in dupes.items():
        ts = bars[indices[0]].get("ts")
        findings.append(Finding(
            code="DUPLICATE_TS",
            severity=Severity.LEAK,
            message=(
                f"timestamp {ts!r} appears {len(indices)} times "
                f"(bars {indices}) - one instant, one bar"
            ),
            where=f"bars[{indices[0]}..{indices[-1]}]",
            detail={"ts": ts, "indices": indices},
        ))
    return findings


def _weekdays_between(d1: date, d2: date) -> int:
    """Count weekdays d with d1 < d <= d2 (0 when d2 <= d1).

    Plain loop on purpose: calendar gaps are days, not microseconds, and
    an obviously-correct loop beats a clever remainder formula here.
    """
    n = 0
    d = d1 + timedelta(days=1)
    while d <= d2:
        if d.weekday() < 5:
            n += 1
        d += timedelta(days=1)
    return n


def check_gaps(
    bars: list[dict],
    expected_days: int = 1,
    max_gap_days: int = 5,
) -> list[Finding]:
    """Trading-calendar police: an unexplained closure is WARN.

    The gap between consecutive bars is counted in *weekdays* - dates d
    with earlier.date() < d <= later.date() and d.weekday() < 5 - so an
    ordinary weekend (Friday -> Monday) is a gap of 1, same as any other
    healthy overnight.  A spacing of more than max_gap_days weekdays is
    a WARN: the backtest silently held through days it has no data for.
    `expected_days` is the cadence you expected (1 = daily); it rides
    along in the finding so the report can say what "normal" was.
    Bars whose ts can't be parsed are transparent here (gaps span them).
    """
    findings: list[Finding] = []
    prev: tuple[int, date] | None = None
    for i, bar in enumerate(bars):
        try:
            d = to_utc(bar.get("ts")).date()
        except (TypeError, ValueError):
            continue
        if prev is not None:
            j, prev_d = prev
            gap = _weekdays_between(prev_d, d)
            if gap > max_gap_days:
                findings.append(Finding(
                    code="CALENDAR_GAP",
                    severity=Severity.WARN,
                    message=(
                        f"{gap} weekdays between bars[{j}] ({bars[j].get('ts')}) and "
                        f"bars[{i}] ({bar.get('ts')}) - expected {expected_days}; "
                        "an unexplained closure"
                    ),
                    where=f"bars[{j}..{i}]",
                    detail={
                        "gap_weekdays": gap,
                        "expected_days": expected_days,
                        "max_gap_days": max_gap_days,
                        "from_ts": bars[j].get("ts"),
                        "to_ts": bar.get("ts"),
                    },
                ))
        prev = (i, d)
    return findings


def check_stale(bars: list[dict], run: int = 4) -> list[Finding]:
    """`run` consecutive identical closes -> WARN: a dead feed or a
    halted symbol.

    Exact equality on close - a stale feed repeats bits, not
    approximately.  One finding per maximal run (six identical closes at
    run=4 is one finding, not three).  Bars whose close isn't a number
    don't count toward a run; check_ohlc already flagged those.
    """
    n = len(bars)
    findings: list[Finding] = []
    i = 0
    while i < n:
        j = i
        while j + 1 < n and bars[j + 1].get("close") == bars[i].get("close"):
            j += 1
        length = j - i + 1
        if length >= run and _finite_number(bars[i].get("close")):
            findings.append(Finding(
                code="STALE_CLOSES",
                severity=Severity.WARN,
                message=(
                    f"{length} consecutive identical closes "
                    f"({bars[i].get('close')!r}) from bars[{i}] to bars[{j}] - "
                    "dead feed or halted symbol"
                ),
                where=f"bars[{i}..{j}]",
                detail={
                    "length": length,
                    "start_ts": bars[i].get("ts"),
                    "end_ts": bars[j].get("ts"),
                    "close": bars[i].get("close"),
                },
            ))
        i = j + 1
    return findings


def check_crossed(bars: list[dict]) -> list[Finding]:
    """A bar carrying both "bid" and "ask" with bid > ask is an
    impossible quote -> LEAK.

    Bars without both fields are not this check's business; missing
    quotes are a data-completeness question, not an impossibility.
    """
    findings: list[Finding] = []
    for i, bar in enumerate(bars):
        bid, ask = bar.get("bid"), bar.get("ask")
        if bid is None or ask is None:
            continue
        if bid > ask:
            findings.append(Finding(
                code="CROSSED_MARKET",
                severity=Severity.LEAK,
                message=(
                    f"bar {i} ({bar.get('ts')}): bid {bid} > ask {ask} - "
                    "a crossed quote is an impossible market"
                ),
                where=f"bars[{i}]",
                detail={"ts": bar.get("ts"), "bid": bid, "ask": ask},
            ))
    return findings


def check_scale_shock(bars: list[dict], z: float = 8.0) -> list[Finding]:
    """A single-bar move bigger than z sigmas of the recent daily-move
    distribution -> WARN: a candidate split or a bad tick.  This check
    only raises its hand; the split detector decides which it was.

    Method, documented rather than tunable (the public signature is
    pinned): returns are log close-to-close, ln(close_i / close_{i-1});
    "recent" is the trailing 30 returns before the candidate, sample
    stdev; a bar is judged only once 8 returns exist before it.  A
    window with sigma <= 0 (perfectly flat history) judges nothing - a
    flat window has no scale, and the stale check already flagged it.
    Bars with non-finite or non-positive closes are skipped here;
    geometry is check_ohlc's job.
    """
    n = len(bars)
    closes = [_usable_close(bar.get("close")) for bar in bars]
    rets: list[float | None] = [None]  # rets[i] links bars[i-1] -> bars[i]
    for i in range(1, n):
        prev_c, cur_c = closes[i - 1], closes[i]
        if prev_c is not None and cur_c is not None:
            rets.append(math.log(cur_c / prev_c))
        else:
            rets.append(None)

    findings: list[Finding] = []
    for i in range(1, n):
        r = rets[i]
        if r is None:
            continue
        window = [x for x in rets[max(0, i - _SHOCK_WINDOW):i] if x is not None]
        if len(window) < _SHOCK_MIN_OBS:
            continue
        sigma = statistics.stdev(window)
        if sigma <= 0:
            continue
        score = abs(r) / sigma
        if score > z:
            findings.append(Finding(
                code="SCALE_SHOCK",
                severity=Severity.WARN,
                message=(
                    f"bar {i} ({bars[i].get('ts')}): move {r:+.4f} is "
                    f"{score:.1f} sigmas of the trailing {_SHOCK_WINDOW}-bar "
                    f"distribution (sigma={sigma:.3e}) - candidate split or bad tick"
                ),
                where=f"bars[{i}]",
                detail={
                    "ts": bars[i].get("ts"),
                    "bar_index": i,
                    "log_return": r,
                    "z_score": score,
                    "sigma": sigma,
                },
            ))
    return findings
