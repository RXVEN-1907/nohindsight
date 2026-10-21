"""nohindsight.forensics.audit — the verdict.

One function, one dataclass.  audit() runs every forensic test this package
knows on one return stream and hands back a Verdict: a level, the numbers
behind it, and the reasons in plain language — because "DSR = 0.37"
convinces nobody, while "the best of 40 luckies scores about 1.1 and yours
barely clears it" does.

Thresholds (documented, deliberately not per-call knobs — a kill switch you
can argue with is a suggestion):
    REAL      dsr_prob >= 0.95 and p < 0.05 and (PBO, when present) < 0.2
    FANTASY   dsr_prob < 0.5  or  p >= 0.2  or (PBO, when present) >= 0.5
    else      SUSPICIOUS

The permutation test is a sign-flip randomization of the null "no edge":
flip every return's sign with probability 1/2, recompute the Sharpe, and
count how often a no-edge path matched or beat yours.  One-sided, free of
distributional assumptions beyond symmetry of the no-edge world.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Optional, Sequence

from .deflate import _hurdle_from_trial_count, deflated_psr, moments
from .pbo import overfit_report
from .stats import _signed_ratio, calmar, max_drawdown, sharpe, sortino, t_stat

__all__ = ["Verdict", "audit"]

REAL = "REAL"
SUSPICIOUS = "SUSPICIOUS"
FANTASY = "FANTASY"


@dataclass
class Verdict:
    """The audit's answer, built to be printed and to be argued with.

    level: one of "REAL", "SUSPICIOUS", "FANTASY".  numbers: every statistic
    computed along the way (floats/ints/None — JSON-friendly, so the report
    layer can serialize it as-is).  reasons: plain-language sentences, one
    per forensic test, each citing the actual numbers it argues about.
    """

    level: str
    numbers: dict = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)


def _permutation_pvalue(
    returns: list[float], observed_sharpe: float, mc_paths: int, ppy: int, seed: int
) -> float:
    """Fraction of sign-flipped no-edge paths whose Sharpe >= the observed.

    Deterministic: one random.Random(seed), exactly mc_paths * n draws, in
    order.  The per-path Sharpe uses the algebraic identity sum(x^2) is
    sign-invariant, so each path costs one pass for the signed sum only;
    the identity yields exactly stats.sharpe's definition (ddof=1), up to
    floating-point summation order, which no comparison here can feel.
    """
    n = len(returns)
    rng = random.Random(seed)
    sumsq = math.fsum(x * x for x in returns)
    at_least = 0
    for _ in range(mc_paths):
        signed = 0.0
        for x in returns:
            if rng.random() < 0.5:
                signed -= x
            else:
                signed += x
        mean = signed / n
        var = (sumsq - n * mean * mean) / (n - 1)
        std = math.sqrt(max(var, 0.0))
        if _signed_ratio(mean, std) * math.sqrt(ppy) >= observed_sharpe:
            at_least += 1
    return at_least / mc_paths


def audit(
    returns: Sequence[float],
    n_trials: int = 1,
    strategy_returns: Optional[Sequence[Sequence[float]]] = None,
    blocks: int = 16,
    ppy: int = 252,
    mc_paths: int = 2000,
    seed: int = 0,
) -> Verdict:
    """Interrogate one return stream and return a Verdict.

    `returns` is the strategy's per-period stream.  `n_trials` is how many
    configurations were tried before this one was chosen — the deflation
    hinge.  When `strategy_returns` (the whole family's matrix, time x
    configs) is supplied, PBO joins the panel; it runs with the pbo module's
    default combination cap.  Deterministic given seed.

    numbers carries: sharpe, sortino, calmar, max_drawdown, t_stat, skew,
    kurtosis, n_obs, n_trials, sr0 (the hurdle the Sharpe had to clear),
    dsr_prob, p_value, mc_paths, pbo (None when no matrix was supplied).
    """
    values = list(returns)
    n = len(values)
    if n < 2:
        raise ValueError(f"audit needs at least 2 observations, got {n}")
    if n_trials < 1:
        raise ValueError(f"n_trials must be >= 1, got {n_trials}")
    if mc_paths < 1:
        raise ValueError(f"mc_paths must be >= 1, got {mc_paths}")

    sr = sharpe(values, ppy)
    t = t_stat(values)
    so = sortino(values, ppy)
    mdd = max_drawdown(values, kind="returns")
    _, _, skew, kurtosis = moments(values)

    # n_trials = 1: no selection ever happened, the hurdle is a zero Sharpe.
    # n_trials > 1 without the trial Sharpes themselves: the count-only
    # unit-variance hurdle from deflate (conservative-ish, documented there).
    sr0 = 0.0 if n_trials == 1 else _hurdle_from_trial_count(n_trials)
    dsr_prob = deflated_psr(values, n_trials, ppy=ppy)
    p_value = _permutation_pvalue(values, sr, mc_paths, ppy, seed)

    pbo_value: Optional[float] = None
    n_combos: Optional[int] = None
    if strategy_returns is not None:
        report = overfit_report(strategy_returns, blocks=blocks, seed=seed)
        pbo_value = report["pbo"]
        n_combos = report["n_combos"]

    reasons = [
        f"Annualized Sharpe is {sr:.2f} over {n} observations "
        f"(t-stat {t:.2f}, max drawdown {mdd:.1%})."
    ]
    if n_trials == 1:
        reasons.append(
            f"With a single trial there is no selection effect to deflate: "
            f"the probability this Sharpe is genuinely above zero is {dsr_prob:.3f}."
        )
    else:
        reasons.append(
            f"You ran {n_trials} trials; the best of {n_trials} pure-luck "
            f"streams scores about {sr0:.2f}, and the probability your "
            f"{sr:.2f} is real anyway is {dsr_prob:.3f}."
        )
    reasons.append(
        f"Flipping every return's sign at random {mc_paths} times, "
        f"{p_value:.1%} of no-edge paths matched or beat your Sharpe "
        f"(p = {p_value:.3f})."
    )
    if pbo_value is not None:
        reasons.append(
            f"Across {n_combos} train/test splits, the in-sample winner fell "
            f"in the worse out-of-sample half {pbo_value:.1%} of the time "
            f"(PBO = {pbo_value:.3f})."
        )

    real_ok = dsr_prob >= 0.95 and p_value < 0.05 and (
        pbo_value is None or pbo_value < 0.2
    )
    fantasy_hit = dsr_prob < 0.5 or p_value >= 0.2 or (
        pbo_value is not None and pbo_value >= 0.5
    )
    if real_ok:
        level = REAL
        reasons.append(
            "Every forensic test passed: this looks like a real edge, "
            "not fitted noise."
        )
    elif fantasy_hit:
        level = FANTASY
        reasons.append(
            "This pattern is indistinguishable from luck: treat the "
            "backtest as entertainment."
        )
    else:
        level = SUSPICIOUS
        reasons.append(
            "Some tests passed and some did not: not enough evidence "
            "either way."
        )

    numbers = {
        "sharpe": sr,
        "sortino": so,
        "calmar": calmar(values, ppy),
        "max_drawdown": mdd,
        "t_stat": t,
        "skew": skew,
        "kurtosis": kurtosis,
        "n_obs": n,
        "n_trials": n_trials,
        "sr0": sr0,
        "dsr_prob": dsr_prob,
        "p_value": p_value,
        "mc_paths": mc_paths,
        "pbo": pbo_value,
    }
    return Verdict(level=level, numbers=numbers, reasons=reasons)
