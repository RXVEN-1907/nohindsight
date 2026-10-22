"""nohindsight.forensics.pbo - probability of backtest overfitting via CSCV.

Combinatorially Symmetric Cross-Validation (Bailey, Borwein, Lopez de Prado,
Zhu 2017).  Cut history into even blocks; over every way of choosing half
the blocks for training, nominate the in-sample winner and record where it
lands out-of-sample on the other half.  A real edge keeps winning where it
was never fitted; an overfit one is a coin flip or worse.  PBO is the
fraction of splits where the in-sample winner finished in the WORSE half
out-of-sample.

Precise conventions (every reimplementation picks these silently and they
disagree, so they are written here):
  * rank 0 = best out-of-sample Sharpe; the rank of a config is the number
    of configs with a STRICTLY higher OOS Sharpe, so a tie at the top
    leaves rank 0 (ties favor the audited config, and say so);
  * rel = rank / (n_configs - 1) in [0, 1]; the logit is
    lam = ln(rel / (1 - rel)), so the best rank maps to lam -> -inf and the
    worst to lam -> +inf;
  * PBO counts combinations with lam >= 0 - the worse half.  A border rank
    (rel exactly 0.5, reachable when n_configs is odd) maps to lam = 0 and
    IS counted as overfit: conservative.  With an even n_configs the
    half-split lands between ranks and there is no border case;
  * the in-sample winner is the argmax of train Sharpes, ties resolved to
    the lowest column index.
"""

from __future__ import annotations

import itertools
import math
import random
import statistics
from typing import Optional, Sequence

from .stats import _signed_ratio

__all__ = ["pbo", "overfit_report"]


def _validate_matrix(
    strategy_returns: Sequence[Sequence[float]], blocks: int
) -> tuple[int, int, int]:
    """Common validation; returns (n_rows, n_configs, rows_per_block)."""
    matrix = list(strategy_returns)
    n_rows = len(matrix)
    if n_rows == 0:
        raise ValueError("strategy_returns is empty")
    try:
        n_configs = len(matrix[0])
    except TypeError:
        raise TypeError(
            "strategy_returns must be a matrix (rows = time, columns = "
            f"configs); got the scalar {matrix[0]!r} - a flat return stream "
            "belongs in audit(returns), not in pbo()"
        ) from None
    if n_configs < 2:
        raise ValueError(
            f"need at least 2 configurations to rank, got {n_configs}"
        )
    for i, row in enumerate(matrix):
        try:
            row_len = len(row)
        except TypeError:
            raise TypeError(
                f"row {i} is a scalar ({row!r}), not a sequence of per-config "
                "returns - the matrix must be rectangular (time x configs)"
            ) from None
        if row_len != n_configs:
            raise ValueError(
                f"row {i} has {row_len} values, expected {n_configs} - "
                "the matrix must be rectangular (time x configs)"
            )
    if blocks < 2 or blocks % 2 != 0:
        raise ValueError(
            f"blocks must be even and >= 2 (half train, half test), got {blocks}"
        )
    rows_per_block = n_rows // blocks
    if rows_per_block < 2:
        raise ValueError(
            f"need at least 2 rows per block for a sample Sharpe: {n_rows} rows "
            f"/ {blocks} blocks = {rows_per_block}"
        )
    return n_rows, n_configs, rows_per_block


def _train_combinations(
    blocks: int, max_combos: int, seed: int
) -> list[tuple[int, ...]]:
    """All C(blocks, blocks/2) train halves, or a seeded unique sample of them.

    Sampling draws train halves directly with random.Random(seed) until
    max_combos distinct halves are collected, with an attempts cap of 50x
    max_combos so a nearly-saturated space cannot spin; the cap can leave
    the sample a few short, and n_combos always reports the true count.
    """
    total = math.comb(blocks, blocks // 2)
    if total <= max_combos:
        return list(itertools.combinations(range(blocks), blocks // 2))
    rng = random.Random(seed)
    combos: list[tuple[int, ...]] = []
    seen: set[tuple[int, ...]] = set()
    attempts = 0
    while len(combos) < max_combos and attempts < 50 * max_combos:
        attempts += 1
        combo = tuple(sorted(rng.sample(range(blocks), blocks // 2)))
        if combo not in seen:
            seen.add(combo)
            combos.append(combo)
    return combos


def _pooled_sharpe(sums: Sequence[float], sumsqs: Sequence[float], n_obs: int) -> float:
    """Sharpe of the concatenation of equal-length blocks from aggregates.

    Mathematically identical to computing stats.sharpe on the concatenated
    rows (the pooled sum of squares collapses to the same sample variance,
    ddof=1), but costs O(blocks) per config instead of O(rows).  The
    zero-dispersion convention is stats._signed_ratio - shared on purpose.
    """
    total = math.fsum(sums)
    mean = total / n_obs
    ss = math.fsum(sumsqs) - n_obs * mean * mean
    std = math.sqrt(max(ss / (n_obs - 1), 0.0))
    return _signed_ratio(mean, std)


def _pbo_detail(
    strategy_returns: Sequence[Sequence[float]],
    blocks: int = 16,
    max_combos: int = 512,
    seed: int = 0,
) -> tuple[float, int, list[int]]:
    """(pbo, n_combos, oos_rank_of_is_best_per_combo) - the shared engine."""
    matrix = [list(row) for row in strategy_returns]
    n_rows, n_configs, rows_per_block = _validate_matrix(matrix, blocks)
    usable = rows_per_block * blocks  # tail rows are trimmed, documented
    if usable < n_rows:
        matrix = matrix[:usable]

    # Per block, per config: (sum, sumsq) over that block's rows.
    sums = [[0.0] * n_configs for _ in range(blocks)]
    sumsqs = [[0.0] * n_configs for _ in range(blocks)]
    for b in range(blocks):
        chunk = matrix[b * rows_per_block : (b + 1) * rows_per_block]
        for row in chunk:
            for c, x in enumerate(row):
                sums[b][c] += x
                sumsqs[b][c] += x * x

    combos = _train_combinations(blocks, max_combos, seed)
    overfit = 0
    ranks: list[int] = []
    half = blocks // 2
    for train in combos:
        test = tuple(b for b in range(blocks) if b not in train)
        n_train = half * rows_per_block
        n_test = n_train

        train_sharpes = [
            _pooled_sharpe([sums[b][c] for b in train], [sumsqs[b][c] for b in train], n_train)
            for c in range(n_configs)
        ]
        best = max(range(n_configs), key=lambda c: train_sharpes[c])

        test_sharpes = [
            _pooled_sharpe([sums[b][c] for b in test], [sumsqs[b][c] for b in test], n_test)
            for c in range(n_configs)
        ]
        rank = sum(1 for c in range(n_configs) if test_sharpes[c] > test_sharpes[best])
        ranks.append(rank)
        rel = rank / (n_configs - 1)
        if rel >= 0.5:  # worse half; border lam = 0 counts, conservative
            overfit += 1

    return overfit / len(combos), len(combos), ranks


def pbo(
    strategy_returns: Sequence[Sequence[float]],
    blocks: int = 16,
    max_combos: int = 512,
    seed: int = 0,
) -> float:
    """Probability of backtest overfitting, in [0, 1].

    `strategy_returns` is a rectangular matrix: rows are time, columns are
    configurations (trials) - matrix[t][c] is config c's return at time t.
    Rows are cut into `blocks` even blocks (a short tail is trimmed);
    whenever there are more than `max_combos` train halves, a seeded unique
    sample of them is used instead (deterministic given seed).

    See the module docstring for the exact rank/logit conventions.
    """
    value, _, _ = _pbo_detail(strategy_returns, blocks, max_combos, seed)
    return value


def overfit_report(
    strategy_returns: Sequence[Sequence[float]],
    blocks: int = 16,
    max_combos: int = 512,
    seed: int = 0,
) -> dict[str, object]:
    """pbo() plus the context needed to argue about it.

    Returns {"pbo", "n_combos", "is_best_oos_median_rank"}: the probability,
    how many train/test splits backed it, and the median out-of-sample rank
    of the in-sample winner (0 = it kept winning, n_configs-1 = it kept
    losing).  A low PBO with a high median rank would be a contradiction -
    if you see one, check the conventions in the module docstring.
    """
    value, n_combos, ranks = _pbo_detail(strategy_returns, blocks, max_combos, seed)
    return {
        "pbo": value,
        "n_combos": n_combos,
        "is_best_oos_median_rank": statistics.median(ranks),
    }
