"""nohindsight.forensics.deflate — the deflated Sharpe ratio.

Reference: Bailey & Lopez de Prado (2014), "The Deflated Sharpe Ratio"
(SSRN 2460551).

A Sharpe measured after picking the best of N trials is not the strategy's
Sharpe — it is the survivor's.  This file prices that difference:

    moments()             the shape statistics the correction needs
    psr()                 probability the observed Sharpe clears a benchmark
    expected_max_sharpe() what the luckiest of N noise traders would score
    deflated_psr()        psr() against that luckiest-of-N hurdle

Everything takes per-period returns (0.01 = 1%).  Phi and Phi-inverse come
from statistics.NormalDist — trusted stdlib, one less thing to get wrong.
"""

from __future__ import annotations

import math
import statistics
from statistics import NormalDist
from typing import Optional, Sequence

from .stats import sharpe

__all__ = ["moments", "psr", "expected_max_sharpe", "deflated_psr"]

_EULER_GAMMA = 0.5772156649015329
_PSR_FLOOR = 1e-12


def moments(returns: Sequence[float]) -> tuple[float, float, float, float]:
    """(mean, std, skew, kurtosis) as population moments over the sample.

    Population (1/n) throughout: these describe the SHAPE of the stream you
    actually observed, not an estimate of some superpopulation.  Kurtosis is
    Pearson's (a normal stream scores 3.0), not the excess form.  A stream
    with no dispersion has no shape to speak of either: std 0.0, skew 0.0,
    kurtosis 3.0 — stated so callers never have to guess.
    """
    values = list(returns)
    n = len(values)
    if n == 0:
        raise ValueError("moments of an empty stream")
    mean = math.fsum(values) / n
    if n == 1:
        return (mean, 0.0, 0.0, 3.0)
    m2 = math.fsum((x - mean) ** 2 for x in values) / n
    if m2 <= 0.0:
        return (mean, 0.0, 0.0, 3.0)
    std = math.sqrt(m2)
    m3 = math.fsum((x - mean) ** 3 for x in values) / n
    m4 = math.fsum((x - mean) ** 4 for x in values) / n
    skew = m3 / std**3
    kurtosis = m4 / m2**2
    return (mean, std, skew, kurtosis)


def _psr_probability(
    sr: float, benchmark_sr: float, n_obs: int, skew: float, kurtosis: float
) -> float:
    """The PSR map itself: Phi of the skew/kurtosis-adjusted z-score.

    Shared by psr() and deflated_psr() so there is exactly one place the
    formula (and its 1e-12 denominator clamp) is written down.
    """
    var_term = 1.0 - skew * sr + ((kurtosis - 1.0) / 4.0) * sr * sr
    denom = math.sqrt(var_term) if var_term > 0.0 else _PSR_FLOOR
    denom = max(denom, _PSR_FLOOR)
    z = (sr - benchmark_sr) * math.sqrt(n_obs - 1) / denom
    return NormalDist().cdf(z)


def psr(
    returns: Sequence[float], benchmark_sr: float, periods_per_year: int = 252
) -> float:
    """Probabilistic Sharpe Ratio: Phi((SR - SR*) * sqrt(n-1) / sqrt(1 - g3*SR + ((g4-1)/4)*SR^2)).

    Convention (the literature wobbles here, so it is stated): SR and SR*
    are ANNUALIZED Sharpes — `returns` is per-period, the benchmark you pass
    is annualized — and the annualized SR is also what appears inside the
    denominator's variance term.  g3 is skew, g4 Pearson kurtosis
    (normal = 3.0).  The denominator is clamped at 1e-12: a degenerate
    variance term means the Sharpe is the whole story, i.e. certainty.

    Reading: the probability that the true Sharpe exceeds SR* given this
    sample.  0.95 is the usual bar for "real".  Raises ValueError on fewer
    than 2 observations.
    """
    values = list(returns)
    n = len(values)
    if n < 2:
        raise ValueError(f"psr needs at least 2 observations, got {n}")
    sr = sharpe(values, periods_per_year)
    _, _, skew, kurtosis = moments(values)
    return _psr_probability(sr, benchmark_sr, n, skew, kurtosis)


def expected_max_sharpe(trial_sharpes: Sequence[float]) -> float:
    """Expected Sharpe of the BEST of N independent trials under the null
    that every trial is noise sharing the observed cross-trial variance:

        SR0 = sqrt(V) * ((1 - gamma) * Z(1 - 1/N) + gamma * Z(1 - 1/(N*e)))

    with gamma the Euler-Mascheroni constant and Z the standard normal
    quantile function.  The cross-trial variance V is the population form
    (statistics.pvariance): the trials you ran ARE the population of
    attempts.

    Degenerate cases, stated: one trial has no selection effect and returns
    that trial's Sharpe; identical trials (zero variance) all tie, so the
    max is the common value; an empty list raises.
    """
    trials = list(trial_sharpes)
    n_trials = len(trials)
    if n_trials == 0:
        raise ValueError("expected max of zero trials")
    if n_trials == 1:
        return float(trials[0])
    variance = statistics.pvariance(trials)
    if variance == 0.0:
        return float(trials[0])
    nd = NormalDist()
    z_best = nd.inv_cdf(1.0 - 1.0 / n_trials)
    z_lucky = nd.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    return math.sqrt(variance) * (
        (1.0 - _EULER_GAMMA) * z_best + _EULER_GAMMA * z_lucky
    )


def _hurdle_from_trial_count(n_trials: int) -> float:
    """SR0 when only the trial COUNT is known: assume unit cross-trial variance.

    An annualized-Sharpe spread of 1.0 across trials is generous for real
    daily strategies, so this hurdle is usually too HIGH — it refuses credit
    more often than the true trials would.  Conservative-ish is the right
    direction to be wrong in for a tool that catches self-deception.
    """
    nd = NormalDist()
    z_best = nd.inv_cdf(1.0 - 1.0 / n_trials)
    z_lucky = nd.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    return (1.0 - _EULER_GAMMA) * z_best + _EULER_GAMMA * z_lucky


def deflated_psr(
    returns: Sequence[float],
    n_trials: int,
    trial_sharpes: Optional[Sequence[float]] = None,
    ppy: int = 252,
) -> float:
    """PSR against the luckiest-of-N hurdle instead of a chosen benchmark.

    With `trial_sharpes` supplied, the hurdle is their expected maximum
    (expected_max_sharpe).  When they are unavailable, only the trial COUNT
    is used and the cross-trial variance is assumed to be 1.0 — a
    conservative-ish fallback, documented in _hurdle_from_trial_count.
    n_trials = 1 means no selection ever happened: the hurdle is a zero
    Sharpe and this collapses to the plain PSR against 0.

    Raises ValueError on n_trials < 1 or fewer than 2 observations.
    """
    values = list(returns)
    n = len(values)
    if n < 2:
        raise ValueError(f"deflated_psr needs at least 2 observations, got {n}")
    if n_trials < 1:
        raise ValueError(f"n_trials must be >= 1, got {n_trials}")
    sr = sharpe(values, ppy)
    _, _, skew, kurtosis = moments(values)
    if trial_sharpes is not None:
        sr0 = expected_max_sharpe(trial_sharpes)
    elif n_trials == 1:
        sr0 = 0.0
    else:
        sr0 = _hurdle_from_trial_count(n_trials)
    return _psr_probability(sr, sr0, n, skew, kurtosis)
