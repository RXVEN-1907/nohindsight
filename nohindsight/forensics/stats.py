"""nohindsight.forensics.stats — the five classical numbers.

Nothing here is clever.  Each function is one formula over a plain list of
per-period returns, written out so a reader can check it against a textbook
in a minute.  The forensic value lives in what deflate/pbo/audit do with
these numbers; this file just refuses to get the arithmetic wrong.

Conventions (stated once, kept everywhere in this package):
  * per-period returns as fractions (0.01 = 1% per period);
  * dispersion is the SAMPLE standard deviation, ddof=1 — a backtest is a
    sample, not a population;
  * annualization multiplies the per-period ratio by sqrt(periods_per_year);
  * a stream with zero dispersion is degenerate, not divine: the ratio
    settles to the sign of the mean (+inf / 0.0 / -inf) so that rank-based
    callers always receive a number they can order.
"""

from __future__ import annotations

import math
from typing import Sequence

__all__ = ["sharpe", "sortino", "max_drawdown", "calmar", "t_stat"]


def _mean_and_sample_std(values: Sequence[float]) -> tuple[float, float]:
    """(mean, sample std with ddof=1), via math.fsum for numerical care.

    Needs at least 2 observations — a dispersion estimate from one point is
    a confession, not a statistic.
    """
    n = len(values)
    if n < 2:
        raise ValueError(f"need at least 2 observations, got {n}")
    mean = math.fsum(values) / n
    ss = math.fsum((x - mean) ** 2 for x in values)
    return mean, math.sqrt(ss / (n - 1))


def _signed_ratio(mean: float, std: float) -> float:
    """mean/std with the zero-dispersion cases settled by the sign of the mean.

    Package-wide convention (see module docstring).  Lives here because
    stats.sharpe is where the convention is first needed; pbo imports it so
    there is exactly one place the degenerate cases are defined.
    """
    if std > 0.0:
        return mean / std
    if mean > 0.0:
        return math.inf
    if mean < 0.0:
        return -math.inf
    return 0.0


def sharpe(returns: Sequence[float], periods_per_year: int = 252) -> float:
    """Annualized Sharpe: (mean / sample std) * sqrt(periods_per_year).

    Zero-dispersion streams resolve to +inf / -inf by the sign of the mean
    (0.0 when the mean is exactly zero) rather than raising — see the module
    docstring.  Raises ValueError on fewer than 2 observations.
    """
    if periods_per_year <= 0:
        raise ValueError(f"periods_per_year must be positive, got {periods_per_year}")
    values = list(returns)
    mean, std = _mean_and_sample_std(values)
    return _signed_ratio(mean, std) * math.sqrt(periods_per_year)


def sortino(returns: Sequence[float], periods_per_year: int = 252) -> float:
    """Annualized Sortino against a MAR of 0: (mean / downside dev) * sqrt(ppy).

    Downside deviation is a population quantity over EVERY observation:
    sqrt((1/n) * sum(min(r, 0)^2)) — periods above target contribute zero,
    they are not dropped.  Convention note: implementations disagree on 1/n
    vs 1/(n-1) and on dropping upside periods; we use the full-sample 1/n
    form and say so.  Zero downside (no losing period at all) resolves like
    sharpe's zero-dispersion case: +inf / -inf / 0.0 by the mean's sign.
    """
    if periods_per_year <= 0:
        raise ValueError(f"periods_per_year must be positive, got {periods_per_year}")
    values = list(returns)
    n = len(values)
    if n < 2:
        raise ValueError(f"need at least 2 observations, got {n}")
    mean = math.fsum(values) / n
    dd = math.sqrt(math.fsum(min(x, 0.0) ** 2 for x in values) / n)
    return _signed_ratio(mean, dd) * math.sqrt(periods_per_year)


def max_drawdown(
    equity_or_returns: Sequence[float], *, kind: str = "auto"
) -> float:
    """Worst peak-to-trough loss as a POSITIVE fraction (0.2 means a 20% dd).

    `equity_or_returns` is either a per-period return stream (compounded
    internally as cumprod(1 + r)) or an equity curve (wealth levels).
    kind="auto" guesses between them: any value <= 0, or every value with
    |v| < 1, reads as returns; anything else reads as equity levels.  The
    guess is a documented rule, not mind-reading — an equity account living
    entirely inside (0, 1) dollars, or per-period returns beyond +/-100%,
    must pass kind explicitly.

    Returns 0.0 for empty or single-point input (no drawdown is possible).
    A leveraged wipeout can push the value past 1.0; that is the arithmetic
    being honest, not a bug.
    """
    if kind not in ("auto", "equity", "returns"):
        raise ValueError(f"kind must be 'auto', 'equity' or 'returns', got {kind!r}")
    values = list(equity_or_returns)
    if kind == "auto":
        looks_like_returns = any(v <= 0.0 for v in values) or all(
            abs(v) < 1.0 for v in values
        )
        kind = "returns" if looks_like_returns else "equity"
    if len(values) < 2:
        return 0.0
    if kind == "returns":
        levels: list[float] = []
        equity = 1.0
        for r in values:
            equity *= 1.0 + r
            levels.append(equity)
    else:
        levels = values
    peak = -math.inf
    worst = 0.0
    for level in levels:
        if level > peak:
            peak = level
        if peak > 0.0:
            worst = max(worst, 1.0 - level / peak)
    return worst


def calmar(returns: Sequence[float], periods_per_year: int = 252) -> float:
    """Calmar: CAGR / max drawdown, both computed from the same stream.

    CAGR = (prod(1 + r)) ** (periods_per_year / n) - 1; the drawdown is
    max_drawdown on the compounded curve.  Assumption: input is per-period
    RETURNS, not equity levels — calmar has no auto-guessing, pass returns.

    A stream with no drawdown settles by the sign of its CAGR (inf for a
    gain, 0.0 for flat).  A total wipeout (some 1 + r <= 0) has no
    logarithmic CAGR; it is scored as CAGR = -1.0 (everything lost is
    everything lost, whatever the path), and the drawdown can exceed 1.0 —
    see max_drawdown.
    """
    values = list(returns)
    n = len(values)
    if n < 1:
        raise ValueError("calmar of an empty stream")
    growth = math.prod(1.0 + r for r in values)
    if growth <= 0.0:
        cagr = -1.0
    else:
        cagr = growth ** (periods_per_year / n) - 1.0
    dd = max_drawdown(values, kind="returns")
    if dd > 0.0:
        return cagr / dd
    return math.inf if cagr > 0.0 else 0.0


def t_stat(returns: Sequence[float]) -> float:
    """t-statistic of the mean: mean / (sample std / sqrt(n)).

    The naive question behind every Sharpe: how many standard errors is the
    average return away from zero?  Zero-dispersion streams resolve like
    sharpe's degenerate case.  Two-sided p-values are the caller's business;
    this hands back the raw statistic.
    """
    values = list(returns)
    mean, std = _mean_and_sample_std(values)
    return _signed_ratio(mean, std) * math.sqrt(len(values))
