"""nohindsight.checks.splits — the unadjusted-split detector.

An unadjusted split left in the bars is free money for a backtest: the
close drops by k overnight and every return, share count, and drawdown
computed on the raw levels is fiction.  The signature of a real split
is a big overnight ratio landing (within tolerance) on a *round* ratio
k, *and* a volume spike — markets don't usually quadruple their volume
on an ordinary bad day.

Both directions count: a 4:1 forward split shows |log return| ~ ln 4,
a 1:4 reverse split shows +ln 4.  The abs() is deliberate; direction
lives in the finding's signed log_return.
"""

from __future__ import annotations

import math

from .base import Finding, Severity

__all__ = ["detect_splits"]

_RATIOS = (2, 3, 4, 5, 10)
# Match tolerance: ||r| - ln k| <= max(TOL_ABS, TOL_REL * ln k).
# Round ratios are landing pads, not sniper targets — a 4:1 split with
# 0.5% of same-day drift must still be caught.
_TOL_REL = 0.02
_TOL_ABS = 0.01


def _matching_ratio(abs_log_return: float) -> int | None:
    for k in _RATIOS:
        target = math.log(k)
        if abs(abs_log_return - target) <= max(_TOL_ABS, _TOL_REL * target):
            return k
    return None


def _positive_finite(value: object) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value) and value > 0


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(value)


def detect_splits(bars: list[dict], vol_mult: float = 3.0) -> list[Finding]:
    """Overnight close-to-close |log return| within tolerance of ln(k)
    for k in {2, 3, 4, 5, 10}, AND volume ratio > vol_mult -> LEAK
    finding with detail {"ratio": k}.

    The volume condition is load-bearing: a crash is not a split.  A bar
    pair with a missing or non-positive previous volume has no ratio and
    no verdict — say nothing rather than guess.  Severity is LEAK because
    an unadjusted split in a backtest produces phantom PnL.
    """
    findings: list[Finding] = []
    for i in range(1, len(bars)):
        prev, cur = bars[i - 1], bars[i]
        prev_close, cur_close = prev.get("close"), cur.get("close")
        if not (_positive_finite(prev_close) and _positive_finite(cur_close)):
            continue
        r = math.log(cur_close / prev_close)
        k = _matching_ratio(abs(r))
        if k is None:
            continue
        prev_vol, cur_vol = prev.get("volume"), cur.get("volume")
        if not (_finite(prev_vol) and _finite(cur_vol)):
            continue
        if prev_vol <= 0:
            continue
        volume_ratio = cur_vol / prev_vol
        if volume_ratio <= vol_mult:
            continue
        findings.append(Finding(
            code="SPLIT_SUSPECTED",
            severity=Severity.LEAK,
            message=(
                f"overnight close ratio {math.exp(r):.3f} at bars[{i}] "
                f"({cur.get('ts')}) — |log return| {abs(r):.3f} lands on ln {k} "
                f"with {volume_ratio:.1f}x volume (> {vol_mult}x): unadjusted "
                "split; raw levels book phantom PnL"
            ),
            where=f"bars[{i}]",
            detail={
                "ratio": k,
                "log_return": r,
                "volume_ratio": volume_ratio,
                "ts": cur.get("ts"),
                "bar_index": i,
            },
        ))
    return findings
