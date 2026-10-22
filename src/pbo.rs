//! nohindsight pbo — probability of backtest overfitting via CSCV.
//!
//! Combinatorially Symmetric Cross-Validation (Bailey, Borwein, Lopez de
//! Prado, Zhu 2017).  Cut history into even blocks; over every way of
//! choosing half the blocks for training, nominate the in-sample winner and
//! record where it lands out-of-sample on the other half.  A real edge keeps
//! winning where it was never fitted; an overfit one is a coin flip or
//! worse.  PBO is the fraction of splits where the in-sample winner finished
//! in the WORSE half out-of-sample.
//!
//! Precise conventions (every reimplementation picks these silently and they
//! disagree, so they are written here):
//!
//! * rank 0 = best out-of-sample Sharpe; the rank of a config is the number
//!   of configs with a STRICTLY higher OOS Sharpe, so a tie at the top
//!   leaves rank 0 (ties favor the audited config, and say so);
//! * rel = rank / (n_configs - 1) in [0, 1]; the logit is
//!   lam = ln(rel / (1 - rel)), so the best rank maps to lam -> -inf and the
//!   worst to lam -> +inf;
//! * PBO counts combinations with rel >= 0.5 — the worse half.  A border
//!   rank (rel exactly 0.5, reachable when n_configs is odd) maps to
//!   lam = 0 and IS counted as overfit: conservative.  With an even
//!   n_configs the half-split lands between ranks and there is no border
//!   case;
//! * the in-sample winner is the argmax of train Sharpes, ties resolved to
//!   the lowest column index.

use crate::rng::SplitMix64;
use crate::stats::{fsum, signed_ratio};
use std::collections::BTreeSet;
use std::error::Error;
use std::fmt;

/// PBO-level input failures.  Messages mirror the Python package's, because
/// the failure modes are the same: a flat stream, a ragged matrix, an odd
/// block count, a block too small to hold a sample Sharpe.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum PboError {
    /// The matrix has no rows.
    EmptyMatrix,
    /// Fewer than 2 configurations cannot be ranked.
    TooFewConfigs { got: usize },
    /// Rows must be rectangular (time x configs).
    RaggedRow {
        row: usize,
        expected: usize,
        got: usize,
    },
    /// blocks must be even and >= 2 (half train, half test).
    BadBlocks { got: usize },
    /// Need at least 2 rows per block for a sample Sharpe.
    TooFewRowsPerBlock {
        n_rows: usize,
        blocks: usize,
        got: usize,
    },
}

impl fmt::Display for PboError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            PboError::EmptyMatrix => write!(f, "strategy_returns is empty"),
            PboError::TooFewConfigs { got } => {
                write!(f, "need at least 2 configurations to rank, got {got}")
            }
            PboError::RaggedRow { row, expected, got } => write!(
                f,
                "row {row} has {got} values, expected {expected} — the matrix \
                 must be rectangular (time x configs)"
            ),
            PboError::BadBlocks { got } => write!(
                f,
                "blocks must be even and >= 2 (half train, half test), got {got}"
            ),
            PboError::TooFewRowsPerBlock {
                n_rows,
                blocks,
                got,
            } => write!(
                f,
                "need at least 2 rows per block for a sample Sharpe: {n_rows} \
                 rows / {blocks} blocks = {got}"
            ),
        }
    }
}

impl Error for PboError {}

/// What the combinations loop is allowed to try, and how it samples.
#[derive(Clone, Copy, Debug)]
pub struct PboConfig {
    /// Even block count; history is cut into this many equal chunks.
    pub blocks: usize,
    /// Above this many train halves, a seeded unique sample is used instead.
    pub max_combos: usize,
    /// Seed for the sampling stream (ignored when enumerating all combos).
    pub seed: u64,
}

impl Default for PboConfig {
    fn default() -> PboConfig {
        PboConfig {
            blocks: 16,
            max_combos: 512,
            seed: 0,
        }
    }
}

/// [`pbo`] plus the context needed to argue about it.
#[derive(Clone, Debug, PartialEq)]
pub struct OverfitReport {
    /// The probability itself, in [0, 1].
    pub pbo: f64,
    /// How many train/test splits backed it.
    pub n_combos: usize,
    /// Median out-of-sample rank of the in-sample winner across splits:
    /// 0 = it kept winning, n_configs-1 = it kept losing.  A low PBO with a
    /// high median rank would be a contradiction — if you see one, check the
    /// conventions in the module docs.
    pub is_best_oos_median_rank: f64,
}

/// C(n, k) as u128 — block counts stay small enough that u128 cannot wrap.
fn combinations_count(n: usize, k: usize) -> u128 {
    let k = k.min(n - k);
    let mut acc: u128 = 1;
    for i in 0..k {
        acc = acc * (n - i) as u128 / (i + 1) as u128;
    }
    acc
}

/// All C(n, k) combinations in lexicographic order — the same order
/// Python's itertools.combinations produces, so enumerated runs line up.
fn all_combinations(n: usize, k: usize) -> Vec<Vec<usize>> {
    let mut out = Vec::new();
    if k == 0 || k > n {
        if k == 0 {
            out.push(Vec::new());
        }
        return out;
    }
    let mut combo: Vec<usize> = (0..k).collect();
    loop {
        out.push(combo.clone());
        // Find the rightmost index that can still advance.
        let mut j = k;
        while j > 0 {
            let idx = j - 1;
            if combo[idx] < n - k + idx {
                break;
            }
            j -= 1;
        }
        if j == 0 {
            return out;
        }
        let idx = j - 1;
        combo[idx] += 1;
        for later in idx + 1..k {
            combo[later] = combo[later - 1] + 1;
        }
    }
}

/// All C(blocks, blocks/2) train halves, or a seeded unique sample of them.
///
/// Sampling draws train halves directly with one `SplitMix64(seed)` until
/// `max_combos` distinct halves are collected, with an attempts cap of 50x
/// max_combos so a nearly-saturated space cannot spin; the cap can leave the
/// sample a few short, and `n_combos` always reports the true count.
fn train_combinations(blocks: usize, max_combos: usize, seed: u64) -> Vec<Vec<usize>> {
    let half = blocks / 2;
    let total = combinations_count(blocks, half);
    if total <= max_combos as u128 {
        return all_combinations(blocks, half);
    }
    let mut rng = SplitMix64::new(seed);
    let mut combos: Vec<Vec<usize>> = Vec::with_capacity(max_combos);
    let mut seen: BTreeSet<Vec<usize>> = BTreeSet::new();
    let mut attempts = 0usize;
    while combos.len() < max_combos && attempts < 50 * max_combos {
        attempts += 1;
        // One sample: k draws of index-or-taken (Fisher-Yates partial
        // shuffle), then sorted.  Documented so the stream is reproducible.
        let mut idxs: Vec<usize> = (0..blocks).collect();
        for pick in 0..half {
            let swap_at = pick + rng.randrange((blocks - pick) as u64) as usize;
            idxs.swap(pick, swap_at);
        }
        idxs.truncate(half);
        idxs.sort_unstable();
        if seen.insert(idxs.clone()) {
            combos.push(idxs);
        }
    }
    combos
}

/// Sharpe of the concatenation of equal-length blocks, from aggregates.
///
/// Mathematically identical to computing `stats::sharpe` on the concatenated
/// rows (the pooled sum of squares collapses to the same sample variance,
/// ddof=1), but costs O(blocks) per config instead of O(rows).  The
/// zero-dispersion convention is `stats::signed_ratio` — shared on purpose.
fn pooled_sharpe(sums: &[f64], sumsqs: &[f64], n_obs: usize) -> f64 {
    let total = fsum(sums.iter().copied());
    let mean = total / n_obs as f64;
    let ss = fsum(sumsqs.iter().copied()) - n_obs as f64 * mean * mean;
    let std = ((ss / (n_obs - 1) as f64).max(0.0)).sqrt();
    signed_ratio(mean, std)
}

fn validate_matrix(matrix: &[Vec<f64>], blocks: usize) -> Result<(usize, usize, usize), PboError> {
    let n_rows = matrix.len();
    if n_rows == 0 {
        return Err(PboError::EmptyMatrix);
    }
    let n_configs = matrix[0].len();
    if n_configs < 2 {
        return Err(PboError::TooFewConfigs { got: n_configs });
    }
    for (i, row) in matrix.iter().enumerate() {
        if row.len() != n_configs {
            return Err(PboError::RaggedRow {
                row: i,
                expected: n_configs,
                got: row.len(),
            });
        }
    }
    if blocks < 2 || blocks % 2 != 0 {
        return Err(PboError::BadBlocks { got: blocks });
    }
    let rows_per_block = n_rows / blocks;
    if rows_per_block < 2 {
        return Err(PboError::TooFewRowsPerBlock {
            n_rows,
            blocks,
            got: rows_per_block,
        });
    }
    Ok((n_rows, n_configs, rows_per_block))
}

fn pbo_detail(
    matrix: &[Vec<f64>],
    config: PboConfig,
) -> Result<(f64, usize, Vec<usize>), PboError> {
    let PboConfig {
        blocks,
        max_combos,
        seed,
    } = config;
    let (n_rows, n_configs, rows_per_block) = validate_matrix(matrix, blocks)?;
    let _ = n_rows; // only needed for the error message inside validate_matrix
    let usable = rows_per_block * blocks; // tail rows are trimmed, documented
    let matrix = &matrix[..usable];

    // Per block, per config: (sum, sumsq) over that block's rows.
    let mut sums = vec![vec![0.0; n_configs]; blocks];
    let mut sumsqs = vec![vec![0.0; n_configs]; blocks];
    for (b, chunk) in matrix.chunks(rows_per_block).enumerate() {
        for row in chunk {
            for (c, x) in row.iter().enumerate() {
                sums[b][c] += x;
                sumsqs[b][c] += x * x;
            }
        }
    }

    let combos = train_combinations(blocks, max_combos, seed);
    let mut overfit = 0usize;
    let mut ranks: Vec<usize> = Vec::with_capacity(combos.len());
    let half = blocks / 2;
    for train in &combos {
        let n_train = half * rows_per_block;

        let train_sharpes: Vec<f64> = (0..n_configs)
            .map(|c| {
                pooled_sharpe(
                    &train.iter().map(|b| sums[*b][c]).collect::<Vec<f64>>(),
                    &train.iter().map(|b| sumsqs[*b][c]).collect::<Vec<f64>>(),
                    n_train,
                )
            })
            .collect();
        let best = train_sharpes
            .iter()
            .enumerate()
            .max_by(|(i, a), (j, b)| {
                // Ties resolve to the LOWEST column index: on equal Sharpes
                // the earlier config must win, so it must compare Greater.
                a.partial_cmp(b)
                    .expect("pooled sharpes are never NaN")
                    .then_with(|| j.cmp(i))
            })
            .map(|(i, _)| i)
            .unwrap_or(0);

        let test: Vec<usize> = (0..blocks).filter(|b| !train.contains(b)).collect();
        let test_sharpes: Vec<f64> = (0..n_configs)
            .map(|c| {
                pooled_sharpe(
                    &test.iter().map(|b| sums[*b][c]).collect::<Vec<f64>>(),
                    &test.iter().map(|b| sumsqs[*b][c]).collect::<Vec<f64>>(),
                    n_train,
                )
            })
            .collect();
        let rank = test_sharpes
            .iter()
            .filter(|s| **s > test_sharpes[best])
            .count();
        ranks.push(rank);
        let rel = rank as f64 / (n_configs - 1) as f64;
        if rel >= 0.5 {
            // worse half; border lam = 0 counts, conservative
            overfit += 1;
        }
    }

    let pbo = overfit as f64 / combos.len() as f64;
    Ok((pbo, combos.len(), ranks))
}

/// Probability of backtest overfitting, in [0, 1].
///
/// `strategy_returns` is a rectangular matrix: rows are time, columns are
/// configurations (trials) — `matrix[t][c]` is config c's return at time t.
/// Rows are cut into `config.blocks` even blocks (a short tail is trimmed);
/// whenever there are more than `config.max_combos` train halves, a seeded
/// unique sample of them is used instead (deterministic given the seed).
///
/// See the module docs for the exact rank/logit conventions.
pub fn pbo(matrix: &[Vec<f64>], config: PboConfig) -> Result<f64, PboError> {
    pbo_detail(matrix, config).map(|(value, _, _)| value)
}

/// [`pbo`] plus the context needed to argue about it.
pub fn overfit_report(matrix: &[Vec<f64>], config: PboConfig) -> Result<OverfitReport, PboError> {
    pbo_detail(matrix, config).map(|(pbo, n_combos, ranks)| {
        let mut sorted = ranks.clone();
        sorted.sort_unstable();
        let mid = sorted.len() / 2;
        let median = if sorted.len() % 2 == 1 {
            sorted[mid] as f64
        } else if sorted.is_empty() {
            0.0
        } else {
            (sorted[mid - 1] + sorted[mid]) as f64 / 2.0
        };
        OverfitReport {
            pbo,
            n_combos,
            is_best_oos_median_rank: median,
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn combination_counts_and_enumeration() {
        assert_eq!(combinations_count(16, 8), 12_870);
        assert_eq!(combinations_count(4, 2), 6);
        assert_eq!(combinations_count(6, 3), 20);
        let combos = all_combinations(4, 2);
        assert_eq!(
            combos,
            vec![
                vec![0, 1],
                vec![0, 2],
                vec![0, 3],
                vec![1, 2],
                vec![1, 3],
                vec![2, 3]
            ]
        );
        // Lexicographic order matches Python's itertools.combinations.
        let combos5 = all_combinations(5, 3);
        assert_eq!(combos5.len(), 10);
        assert_eq!(combos5[0], vec![0, 1, 2]);
        assert_eq!(combos5[9], vec![2, 3, 4]);
    }

    #[test]
    fn sampled_combos_are_unique_and_deterministic() {
        let a = train_combinations(20, 50, 3);
        let b = train_combinations(20, 50, 3);
        assert_eq!(a, b, "same seed, same sample");
        assert_eq!(a.len(), 50);
        let uniq: BTreeSet<&Vec<usize>> = a.iter().collect();
        assert_eq!(uniq.len(), 50, "samples must be distinct");
        for combo in &a {
            assert_eq!(combo.len(), 10);
            assert!(combo.windows(2).all(|w| w[0] < w[1]), "sorted halves");
        }
        // A space smaller than the cap enumerates everything instead.
        assert_eq!(train_combinations(6, 100, 3).len(), 20);
    }

    #[test]
    fn pooled_sharpe_matches_the_direct_computation() {
        // The aggregates identity must equal stats::sharpe on the flat rows.
        let rows: Vec<f64> = (0..50)
            .map(|i| ((i as f64) * 0.618_033_988_749_895).sin() * 0.01)
            .collect();
        let direct = crate::stats::sharpe(&rows, 1).expect("n >= 2");
        let pooled = pooled_sharpe(
            &[fsum(rows.iter().copied())],
            &[fsum(rows.iter().map(|x| x * x))],
            rows.len(),
        );
        assert!(
            (direct - pooled).abs() < 1e-9,
            "direct {direct} pooled {pooled}"
        );
    }

    #[test]
    fn validation_rejects_the_documented_shapes() {
        let m: Vec<Vec<f64>> = (0..40).map(|_| vec![0.01, 0.02]).collect();
        assert!(pbo(&[], PboConfig::default()).is_err());
        assert_eq!(
            pbo(
                &(0..40).map(|_| vec![0.01]).collect::<Vec<Vec<f64>>>(),
                PboConfig::default()
            ),
            Err(PboError::TooFewConfigs { got: 1 })
        );
        let ragged = vec![vec![0.01, 0.02], vec![0.01]];
        assert!(pbo(&ragged, PboConfig::default()).is_err());
        assert_eq!(
            pbo(
                &m,
                PboConfig {
                    blocks: 5,
                    ..PboConfig::default()
                }
            ),
            Err(PboError::BadBlocks { got: 5 })
        );
        assert!(pbo(
            &m,
            PboConfig {
                blocks: 100,
                ..PboConfig::default()
            }
        )
        .is_err());
    }
}
