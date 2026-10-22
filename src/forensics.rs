//! nohindsight forensics - is it luck?
//!
//! The police in [`crate::checks`] catch a dirty backtest; this module
//! interrogates a CLEAN one.  It runs every forensic test the engine knows
//! on one return stream and hands back a [`Verdict`]: a level, the numbers
//! behind it, and the reasons in plain language - because "DSR = 0.37"
//! convinces nobody, while "the best of 40 luckies scores about 1.1 and
//! yours barely clears it" does.
//!
//! Thresholds (documented, deliberately not per-call knobs - a kill switch
//! you can argue with is a suggestion):
//!
//! * REAL      - dsr_prob >= 0.95 and p < 0.05 and (PBO, when present) < 0.2
//! * FANTASY   - dsr_prob < 0.5  or  p >= 0.2  or (PBO, when present) >= 0.5
//! * else      - SUSPICIOUS
//!
//! The permutation test is a sign-flip randomization of the null "no edge":
//! flip every return's sign with probability 1/2, recompute the Sharpe, and
//! count how often a no-edge path matched or beat yours.  One-sided, free of
//! distributional assumptions beyond symmetry of the no-edge world.
//!
//! Reference: Bailey & Lopez de Prado (2014), "The Deflated Sharpe Ratio"
//! (SSRN 2460551).

use crate::pbo::{overfit_report, PboConfig};
use crate::rng::SplitMix64;
use crate::stats::{
    calmar, fsum, inv_normal_cdf, max_drawdown, moments, normal_cdf, sharpe, signed_ratio, sortino,
    t_stat, DdKind, StatsError,
};
use std::error::Error;
use std::fmt;

/// The Euler-Mascheroni constant, to full double precision.
pub const EULER_GAMMA: f64 = 0.577_215_664_901_532_9;
const PSR_FLOOR: f64 = 1e-12;

/// Forensics-level failures - thin wrappers that keep the Python messages.
#[derive(Clone, Debug, PartialEq)]
pub enum ForensicsError {
    /// A stats function refused its input.
    Stats(StatsError),
    /// The PBO panel refused its matrix.
    Pbo(crate::pbo::PboError),
    /// Fewer than 2 observations - no dispersion, no forensics.
    NotEnoughObservations {
        what: String,
        need: usize,
        got: usize,
    },
    /// The deflation hinge must be >= 1.
    BadTrials(usize),
    /// The permutation test needs at least one path.
    BadMcPaths(usize),
}

impl fmt::Display for ForensicsError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ForensicsError::Stats(e) => write!(f, "{e}"),
            ForensicsError::Pbo(e) => write!(f, "pbo: {e}"),
            ForensicsError::NotEnoughObservations { what, need, got } => {
                write!(f, "{what} needs at least {need} observations, got {got}")
            }
            ForensicsError::BadTrials(n) => {
                write!(f, "n_trials must be >= 1, got {n}")
            }
            ForensicsError::BadMcPaths(n) => {
                write!(f, "mc_paths must be >= 1, got {n}")
            }
        }
    }
}

impl Error for ForensicsError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            ForensicsError::Stats(e) => Some(e),
            ForensicsError::Pbo(e) => Some(e),
            _ => None,
        }
    }
}

impl From<StatsError> for ForensicsError {
    fn from(e: StatsError) -> Self {
        ForensicsError::Stats(e)
    }
}

impl From<crate::pbo::PboError> for ForensicsError {
    fn from(e: crate::pbo::PboError) -> Self {
        ForensicsError::Pbo(e)
    }
}

// ---------------------------------------------------------------------------
// PSR and the deflation machinery
// ---------------------------------------------------------------------------

/// The PSR map itself: Phi of the skew/kurtosis-adjusted z-score.
///
/// Shared by [`psr`] and [`deflated_psr`] so there is exactly one place the
/// formula (and its 1e-12 denominator clamp) is written down.
fn psr_probability(sr: f64, benchmark_sr: f64, n_obs: usize, skew: f64, kurtosis: f64) -> f64 {
    let var_term = 1.0 - skew * sr + ((kurtosis - 1.0) / 4.0) * sr * sr;
    let denom = if var_term > 0.0 {
        var_term.sqrt()
    } else {
        PSR_FLOOR
    };
    let denom = denom.max(PSR_FLOOR);
    let z = (sr - benchmark_sr) * ((n_obs - 1) as f64).sqrt() / denom;
    normal_cdf(z)
}

/// Probabilistic Sharpe Ratio:
/// Phi((SR - SR*) * sqrt(n-1) / sqrt(1 - g3*SR + ((g4-1)/4)*SR^2)).
///
/// Convention (the literature wobbles here, so it is stated): SR and SR* are
/// ANNUALIZED Sharpes - `returns` is per-period, the benchmark you pass is
/// annualized - and the annualized SR is also what appears inside the
/// denominator's variance term.  g3 is skew, g4 Pearson kurtosis
/// (normal = 3.0).  The denominator is clamped at 1e-12: a degenerate
/// variance term means the Sharpe is the whole story, i.e. certainty.
///
/// Reading: the probability that the true Sharpe exceeds SR* given this
/// sample.  0.95 is the usual bar for "real".
pub fn psr(
    returns: &[f64],
    benchmark_sr: f64,
    periods_per_year: u32,
) -> Result<f64, ForensicsError> {
    let n = returns.len();
    if n < 2 {
        return Err(ForensicsError::NotEnoughObservations {
            what: "psr".to_string(),
            need: 2,
            got: n,
        });
    }
    let sr = sharpe(returns, periods_per_year)?;
    let (_, _, skew, kurtosis) = moments(returns)?;
    Ok(psr_probability(sr, benchmark_sr, n, skew, kurtosis))
}

/// Expected Sharpe of the BEST of N independent trials under the null
/// that every trial is noise sharing the observed cross-trial variance:
///
/// `SR0 = sqrt(V) * ((1 - gamma) * Z(1 - 1/N) + gamma * Z(1 - 1/(N*e)))`
///
/// with gamma the Euler-Mascheroni constant and Z the standard normal
/// quantile function.  The cross-trial variance V is the population form:
/// the trials you ran ARE the population of attempts.
///
/// Degenerate cases, stated: one trial has no selection effect and returns
/// that trial's Sharpe; identical trials (zero variance) all tie, so the max
/// is the common value; an empty list is an error.
pub fn expected_max_sharpe(trial_sharpes: &[f64]) -> Result<f64, ForensicsError> {
    let n_trials = trial_sharpes.len();
    if n_trials == 0 {
        return Err(ForensicsError::Stats(StatsError::EmptyStream {
            what: "expected max of zero trials".to_string(),
        }));
    }
    if n_trials == 1 {
        return Ok(trial_sharpes[0]);
    }
    let mean = fsum(trial_sharpes.iter().copied()) / n_trials as f64;
    let variance = fsum(trial_sharpes.iter().map(|x| (x - mean) * (x - mean))) / n_trials as f64;
    if variance == 0.0 {
        return Ok(trial_sharpes[0]);
    }
    let z_best = inv_normal_cdf(1.0 - 1.0 / n_trials as f64)?;
    let z_lucky = inv_normal_cdf(1.0 - 1.0 / (n_trials as f64 * std::f64::consts::E))?;
    Ok(variance.sqrt() * ((1.0 - EULER_GAMMA) * z_best + EULER_GAMMA * z_lucky))
}

/// SR0 when only the trial COUNT is known: assume unit cross-trial variance.
///
/// An annualized-Sharpe spread of 1.0 across trials is generous for real
/// daily strategies, so this hurdle is usually too HIGH - it refuses credit
/// more often than the true trials would.  Conservative-ish is the right
/// direction to be wrong in for a tool that catches self-deception.
pub fn hurdle_from_trial_count(n_trials: usize) -> Result<f64, ForensicsError> {
    if n_trials < 2 {
        return Err(ForensicsError::BadTrials(n_trials));
    }
    let z_best = inv_normal_cdf(1.0 - 1.0 / n_trials as f64)?;
    let z_lucky = inv_normal_cdf(1.0 - 1.0 / (n_trials as f64 * std::f64::consts::E))?;
    Ok((1.0 - EULER_GAMMA) * z_best + EULER_GAMMA * z_lucky)
}

/// PSR against the luckiest-of-N hurdle instead of a chosen benchmark.
///
/// With `trial_sharpes` supplied, the hurdle is their expected maximum
/// ([`expected_max_sharpe`]).  When they are unavailable, only the trial
/// COUNT is used and the cross-trial variance is assumed to be 1.0 - a
/// conservative-ish fallback, documented in [`hurdle_from_trial_count`].
/// `n_trials = 1` means no selection ever happened: the hurdle is a zero
/// Sharpe and this collapses to the plain PSR against 0.
pub fn deflated_psr(
    returns: &[f64],
    n_trials: usize,
    trial_sharpes: Option<&[f64]>,
    periods_per_year: u32,
) -> Result<f64, ForensicsError> {
    let n = returns.len();
    if n < 2 {
        return Err(ForensicsError::NotEnoughObservations {
            what: "deflated_psr".to_string(),
            need: 2,
            got: n,
        });
    }
    if n_trials < 1 {
        return Err(ForensicsError::BadTrials(n_trials));
    }
    let sr = sharpe(returns, periods_per_year)?;
    let (_, _, skew, kurtosis) = moments(returns)?;
    let sr0 = match trial_sharpes {
        Some(trials) => expected_max_sharpe(trials)?,
        None if n_trials == 1 => 0.0,
        None => hurdle_from_trial_count(n_trials)?,
    };
    Ok(psr_probability(sr, sr0, n, skew, kurtosis))
}

/// Fraction of sign-flipped no-edge paths whose Sharpe >= the observed.
///
/// Deterministic: one `SplitMix64(seed)`, exactly mc_paths * n draws, in
/// order - per path, one draw per return, flip when the draw is < 0.5.  The
/// per-path Sharpe uses the algebraic identity sum(x^2) is sign-invariant,
/// so each path costs one pass for the signed sum only; the identity yields
/// exactly `stats::sharpe`'s definition (ddof=1), up to floating-point
/// summation order, which no comparison here can feel.
pub fn permutation_pvalue(
    returns: &[f64],
    observed_sharpe: f64,
    mc_paths: usize,
    periods_per_year: u32,
    seed: u64,
) -> Result<f64, ForensicsError> {
    let n = returns.len();
    if n < 2 {
        return Err(ForensicsError::NotEnoughObservations {
            what: "permutation test".to_string(),
            need: 2,
            got: n,
        });
    }
    if mc_paths < 1 {
        return Err(ForensicsError::BadMcPaths(mc_paths));
    }
    let ann = (periods_per_year as f64).sqrt();
    let mut rng = SplitMix64::new(seed);
    let sumsq = fsum(returns.iter().map(|x| x * x));
    let mut at_least = 0usize;
    for _ in 0..mc_paths {
        let mut signed = 0.0;
        for x in returns {
            if rng.below(0.5) {
                signed -= x;
            } else {
                signed += x;
            }
        }
        let mean = signed / n as f64;
        let var = (sumsq - n as f64 * mean * mean) / (n - 1) as f64;
        let std = var.max(0.0).sqrt();
        if signed_ratio(mean, std) * ann >= observed_sharpe {
            at_least += 1;
        }
    }
    Ok(at_least as f64 / mc_paths as f64)
}

// ---------------------------------------------------------------------------
// the verdict
// ---------------------------------------------------------------------------

/// The audit's verdict level.  Ordering is display order, not a ranking:
/// a SUSPICIOUS is not "half a REAL".
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Level {
    /// Every forensic test passed.
    Real,
    /// Some tests passed and some did not.
    Suspicious,
    /// Indistinguishable from luck.
    Fantasy,
}

impl Level {
    /// The exact string the Python engine prints.
    pub fn as_str(self) -> &'static str {
        match self {
            Level::Real => "REAL",
            Level::Suspicious => "SUSPICIOUS",
            Level::Fantasy => "FANTASY",
        }
    }
}

impl fmt::Display for Level {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

/// Every statistic the audit computed along the way - typed, so nothing can
/// disagree with its own parts.  `pbo`/`n_combos` are None when no strategy
/// matrix was supplied.
#[derive(Clone, Debug, PartialEq)]
pub struct AuditNumbers {
    pub sharpe: f64,
    pub sortino: f64,
    pub calmar: f64,
    pub max_drawdown: f64,
    pub t_stat: f64,
    pub skew: f64,
    pub kurtosis: f64,
    pub n_obs: usize,
    pub n_trials: usize,
    /// The hurdle the Sharpe had to clear.
    pub sr0: f64,
    pub dsr_prob: f64,
    pub p_value: f64,
    pub mc_paths: usize,
    pub pbo: Option<f64>,
    pub n_combos: Option<usize>,
}

/// The audit's answer, built to be printed and to be argued with.
#[derive(Clone, Debug, PartialEq)]
pub struct Verdict {
    pub level: Level,
    pub numbers: AuditNumbers,
    /// Plain-language sentences, one per forensic test, each citing the
    /// actual numbers it argues about.
    pub reasons: Vec<String>,
}

/// Knobs the audit takes, with the engine defaults.
#[derive(Clone, Copy, Debug)]
pub struct AuditConfig {
    /// How many configurations were tried before this one was chosen - the
    /// deflation hinge.
    pub n_trials: usize,
    /// Periods per year, for annualization.
    pub ppy: u32,
    /// Sign-flip paths for the permutation test.
    pub mc_paths: usize,
    /// Seed for the permutation stream.
    pub seed: u64,
    /// Blocks for the PBO panel, when a strategy matrix is supplied.
    pub blocks: usize,
}

impl Default for AuditConfig {
    fn default() -> AuditConfig {
        AuditConfig {
            n_trials: 1,
            ppy: 252,
            mc_paths: 2000,
            seed: 0,
            blocks: 16,
        }
    }
}

/// Interrogate one return stream and return a [`Verdict`].
///
/// `returns` is the strategy's per-period stream.  `cfg.n_trials` is how
/// many configurations were tried before this one was chosen - the deflation
/// hinge.  When `strategy_returns` (the whole family's matrix, time x
/// configs) is supplied, PBO joins the panel; it runs with the PBO module's
/// default combination cap.  Deterministic given `cfg.seed`.
pub fn audit(
    returns: &[f64],
    cfg: AuditConfig,
    strategy_returns: Option<&[Vec<f64>]>,
) -> Result<Verdict, ForensicsError> {
    let AuditConfig {
        n_trials,
        ppy,
        mc_paths,
        seed,
        blocks,
    } = cfg;
    let n = returns.len();
    if n < 2 {
        return Err(ForensicsError::NotEnoughObservations {
            what: "audit".to_string(),
            need: 2,
            got: n,
        });
    }
    if n_trials < 1 {
        return Err(ForensicsError::BadTrials(n_trials));
    }
    if mc_paths < 1 {
        return Err(ForensicsError::BadMcPaths(mc_paths));
    }

    let sr = sharpe(returns, ppy)?;
    let t = t_stat(returns)?;
    let so = sortino(returns, ppy)?;
    let mdd = max_drawdown(returns, DdKind::Returns);
    let (_, _, skew, kurtosis) = moments(returns)?;

    // n_trials = 1: no selection ever happened, the hurdle is a zero Sharpe.
    // n_trials > 1 without the trial Sharpes themselves: the count-only
    // unit-variance hurdle (conservative-ish, documented there).
    let sr0 = if n_trials == 1 {
        0.0
    } else {
        hurdle_from_trial_count(n_trials)?
    };
    let dsr_prob = deflated_psr(returns, n_trials, None, ppy)?;
    let p_value = permutation_pvalue(returns, sr, mc_paths, ppy, seed)?;

    let mut pbo_value = None;
    let mut n_combos = None;
    if let Some(matrix) = strategy_returns {
        let report = overfit_report(
            matrix,
            PboConfig {
                blocks,
                ..PboConfig::default()
            },
        )?;
        pbo_value = Some(report.pbo);
        n_combos = Some(report.n_combos);
    }

    let pct = |x: f64| format!("{:.1}", x * 100.0);
    let mut reasons = vec![format!(
        "Annualized Sharpe is {sr:.2} over {n} observations \
         (t-stat {t:.2}, max drawdown {}%).",
        pct(mdd)
    )];
    if n_trials == 1 {
        reasons.push(format!(
            "With a single trial there is no selection effect to deflate: \
             the probability this Sharpe is genuinely above zero is {dsr_prob:.3}."
        ));
    } else {
        reasons.push(format!(
            "You ran {n_trials} trials; the best of {n_trials} pure-luck \
             streams scores about {sr0:.2}, and the probability your {sr:.2} \
             is real anyway is {dsr_prob:.3}."
        ));
    }
    reasons.push(format!(
        "Flipping every return's sign at random {mc_paths} times, \
         {}% of no-edge paths matched or beat your Sharpe (p = {p_value:.3}).",
        pct(p_value)
    ));
    if let (Some(pbo), Some(combos)) = (pbo_value, n_combos) {
        reasons.push(format!(
            "Across {combos} train/test splits, the in-sample winner fell \
             in the worse out-of-sample half {}% of the time (PBO = {pbo:.3}).",
            pct(pbo)
        ));
    }

    let real_ok = dsr_prob >= 0.95
        && p_value < 0.05
        && match pbo_value {
            Some(v) => v < 0.2,
            None => true,
        };
    let fantasy_hit = dsr_prob < 0.5 || p_value >= 0.2 || pbo_value.is_some_and(|v| v >= 0.5);
    let (level, closing) = if real_ok {
        (
            Level::Real,
            "Every forensic test passed: this looks like a real edge, \
             not fitted noise.",
        )
    } else if fantasy_hit {
        (
            Level::Fantasy,
            "This pattern is indistinguishable from luck: treat the \
             backtest as entertainment.",
        )
    } else {
        (
            Level::Suspicious,
            "Some tests passed and some did not: not enough evidence \
             either way.",
        )
    };
    reasons.push(closing.to_string());

    Ok(Verdict {
        level,
        numbers: AuditNumbers {
            sharpe: sr,
            sortino: so,
            calmar: calmar(returns, ppy)?,
            max_drawdown: mdd,
            t_stat: t,
            skew,
            kurtosis,
            n_obs: n,
            n_trials,
            sr0,
            dsr_prob,
            p_value,
            mc_paths,
            pbo: pbo_value,
            n_combos,
        },
        reasons,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn psr_matches_the_python_reference() {
        // Worked example pinned against the Python package (same formulas,
        // same conventions, independent implementation).
        let s = [0.03, -0.01, 0.02, -0.02, 0.04];
        let got = psr(&s, 1.0, 252).expect("ok");
        assert!((got - 0.999_995_718_135_492_2).abs() < 1e-9, "psr {got}");
        let got = psr(&s, 0.0, 252).expect("ok");
        assert!((got - 0.999_999_870_050_615_4).abs() < 1e-9, "psr {got}");
        assert!(psr(&[0.01], 1.0, 252).is_err());
    }

    #[test]
    fn psr_monotone_in_benchmark() {
        let s = [0.03, -0.01, 0.02, -0.02, 0.04];
        let mut prev = 1.1;
        for bench in [0.0, 0.5, 1.0, 2.0, 4.0] {
            let p = psr(&s, bench, 252).expect("ok");
            assert!(p <= prev + 1e-15, "psr must fall as the bar rises");
            prev = p;
        }
    }

    #[test]
    fn expected_max_sharpe_matches_reference_and_grows() {
        let trials = [1.0, 1.2, 0.8, 1.5, 0.9];
        let got = expected_max_sharpe(&trials).expect("ok");
        assert!((got - 0.295_994_046_895_200_37).abs() < 1e-9, "emax {got}");
        // One trial: no selection effect.
        assert_eq!(expected_max_sharpe(&[0.7]).expect("ok"), 0.7);
        // Identical trials tie; the max is the common value.
        assert_eq!(expected_max_sharpe(&[0.9, 0.9, 0.9]).expect("ok"), 0.9);
        // More trials, same spread: the luckiest of N keeps rising.
        let unit = |n: usize| -> f64 {
            let trials: Vec<f64> = (0..n)
                .map(|i| if i % 2 == 0 { 1.0 } else { -1.0 })
                .collect();
            expected_max_sharpe(&trials).expect("ok")
        };
        assert!(unit(4) < unit(40) && unit(40) < unit(400));
    }

    #[test]
    fn hurdle_matches_reference() {
        assert!((hurdle_from_trial_count(5).expect("ok") - 1.192_594_001_014_789).abs() < 1e-9);
        assert!((hurdle_from_trial_count(40).expect("ok") - 2.189_480_227_537_174_5).abs() < 1e-9);
        assert!(hurdle_from_trial_count(1).is_err());
    }

    #[test]
    fn dsr_falls_as_trials_rise() {
        let s = [0.03, -0.01, 0.02, -0.02, 0.04];
        let d1 = deflated_psr(&s, 1, None, 252).expect("ok");
        let d5 = deflated_psr(&s, 5, None, 252).expect("ok");
        let d50 = deflated_psr(&s, 50, None, 252).expect("ok");
        assert!((d1 - 0.999_999_870_050_615_4).abs() < 1e-9);
        assert!((d5 - 0.999_992_048_649_743_6).abs() < 1e-9);
        assert!(d1 >= d5 && d5 >= d50, "deflation is monotone in trials");
    }

    #[test]
    fn permutation_pvalue_separates_drift_from_noise() {
        // Strong drift: essentially no no-edge path matches it.
        let drift: Vec<f64> = (0..400)
            .map(|i| if i % 2 == 0 { 0.02 } else { -0.004 })
            .collect();
        let obs = sharpe(&drift, 252).expect("ok");
        let p = permutation_pvalue(&drift, obs, 500, 252, 0).expect("ok");
        assert!(p <= 0.01, "drift p {p}");
        // Exact zero-mean noise: the observed Sharpe is unremarkable.
        let noise: Vec<f64> = (0..400)
            .map(|i| if i % 2 == 0 { 0.01 } else { -0.01 })
            .collect();
        let obs = sharpe(&noise, 252).expect("ok");
        let p = permutation_pvalue(&noise, obs, 500, 252, 0).expect("ok");
        assert!(p > 0.3, "noise p {p}");
        // Deterministic given the seed.
        let again = permutation_pvalue(&noise, obs, 500, 252, 0).expect("ok");
        assert_eq!(p, again);
    }

    #[test]
    fn audit_levels_across_crafted_inputs() {
        // A strong real drift, one trial: every test passes -> REAL.
        let drift: Vec<f64> = (0..600)
            .map(|i| if i % 2 == 0 { 0.015 } else { -0.005 })
            .collect();
        let v = audit(
            &drift,
            AuditConfig {
                mc_paths: 400,
                ..AuditConfig::default()
            },
            None,
        )
        .expect("ok");
        assert_eq!(v.level, Level::Real);
        assert!(v.reasons.iter().any(|r| r.contains("real edge")));

        // Exact zero-mean noise, one trial: p is huge -> FANTASY.
        let noise: Vec<f64> = (0..600)
            .map(|i| if i % 2 == 0 { 0.01 } else { -0.01 })
            .collect();
        let v = audit(
            &noise,
            AuditConfig {
                mc_paths: 400,
                ..AuditConfig::default()
            },
            None,
        )
        .expect("ok");
        assert_eq!(v.level, Level::Fantasy);
        assert!(v
            .reasons
            .iter()
            .any(|r| r.contains("indistinguishable from luck")));

        // The three forensic-test reasons cite their numbers; the closing
        // reason is the verdict's plain sentence and needs none.
        for reason in v.reasons.iter().take(3) {
            assert!(reason.chars().any(|c| c.is_ascii_digit()), "{reason}");
        }
        assert!(audit(&[0.01], AuditConfig::default(), None).is_err());
        assert!(audit(
            &drift,
            AuditConfig {
                n_trials: 0,
                ..AuditConfig::default()
            },
            None
        )
        .is_err());
    }
}
