//! nohindsight stats - the five classical numbers, plus the normal curve.
//!
//! Nothing here is clever.  Each function is one formula over a slice of
//! per-period returns, written out so a reader can check it against a
//! textbook in a minute.  The forensic value lives in what
//! [`crate::forensics`] does with these numbers; this file just refuses to
//! get the arithmetic wrong.
//!
//! Conventions (stated once, kept everywhere):
//!
//! * per-period returns as fractions (0.01 = 1% per period);
//! * dispersion for Sharpe/t-stat is the SAMPLE standard deviation, ddof=1 -
//!   a backtest is a sample, not a population;
//! * the shape moments ([`moments`]) are POPULATION moments over the sample
//!   (1/n): they describe the shape of the stream you actually observed, and
//!   kurtosis is Pearson's (a normal stream scores 3.0), not the excess form;
//! * annualization multiplies the per-period ratio by sqrt(periods_per_year);
//! * a stream with zero dispersion is degenerate, not divine: the ratio
//!   settles to the sign of the mean (+inf / 0.0 / -inf) so rank-based
//!   callers always receive a number they can order.
//!
//! The normal curve: [`normal_cdf`] goes through a hand-rolled `erfc` (no
//! libm crate) - an alternating power series for the small-argument branch
//! and a modified-Lentz continued fraction (DLMF 7.9.1) for the tail, each
//! good to a few ulps; [`inv_normal_cdf`] is Acklam's rational inverse with
//! one Halley refinement step, which lands near machine precision (target
//! ~1e-9 relative; the tests hold it to that against known constants).

use std::error::Error;
use std::fmt;

/// Stats-level input failures.
#[derive(Clone, Debug, PartialEq)]
pub enum StatsError {
    /// A dispersion estimate from one point is a confession, not a statistic.
    NotEnoughObservations {
        what: String,
        need: usize,
        got: usize,
    },
    /// periods_per_year must be positive.
    ZeroPeriods,
    /// calmar of an empty stream / moments of an empty stream.
    EmptyStream { what: String },
    /// A probability outside (0, 1) has no quantile.
    BadProbability(f64),
}

impl fmt::Display for StatsError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            StatsError::NotEnoughObservations { what, need, got } => {
                write!(f, "{what} needs at least {need} observations, got {got}")
            }
            StatsError::ZeroPeriods => {
                write!(f, "periods_per_year must be positive")
            }
            StatsError::EmptyStream { what } => write!(f, "{what} of an empty stream"),
            StatsError::BadProbability(p) => {
                write!(f, "probability must be in (0, 1), got {p}")
            }
        }
    }
}

impl Error for StatsError {}

/// Compensated (Neumaier) summation - the zero-dependency stand-in for
/// math.fsum.  Errors that a plain running sum would lose to cancellation
/// are carried in a second accumulator instead.
pub fn fsum(values: impl IntoIterator<Item = f64>) -> f64 {
    let mut sum = 0.0;
    let mut comp = 0.0;
    for x in values {
        let t = sum + x;
        if sum.abs() >= x.abs() {
            comp += (sum - t) + x;
        } else {
            comp += (x - t) + sum;
        }
        sum = t;
    }
    sum + comp
}

/// mean/std(ddof=1), via [`fsum`] for numerical care.
///
/// Needs at least 2 observations - a dispersion estimate from one point is
/// a confession, not a statistic.
pub fn mean_and_sample_std(values: &[f64]) -> Result<(f64, f64), StatsError> {
    let n = values.len();
    if n < 2 {
        return Err(StatsError::NotEnoughObservations {
            what: "mean and sample std".to_string(),
            need: 2,
            got: n,
        });
    }
    let mean = fsum(values.iter().copied()) / n as f64;
    let ss = fsum(values.iter().map(|x| (x - mean) * (x - mean)));
    Ok((mean, (ss / (n - 1) as f64).sqrt()))
}

/// mean/std with the zero-dispersion cases settled by the sign of the mean.
/// Package-wide convention (see the module docs); lives here because Sharpe
/// is where the convention is first needed, and the forensics and PBO files
/// import it so the degenerate cases are defined in exactly one place.
pub fn signed_ratio(mean: f64, std: f64) -> f64 {
    if std > 0.0 {
        mean / std
    } else if mean > 0.0 {
        f64::INFINITY
    } else if mean < 0.0 {
        f64::NEG_INFINITY
    } else {
        0.0
    }
}

/// Annualized Sharpe: (mean / sample std) * sqrt(periods_per_year).
///
/// Zero-dispersion streams resolve to +inf / -inf by the sign of the mean
/// (0.0 when the mean is exactly zero) rather than raising.
pub fn sharpe(returns: &[f64], periods_per_year: u32) -> Result<f64, StatsError> {
    if periods_per_year == 0 {
        return Err(StatsError::ZeroPeriods);
    }
    let (mean, std) = mean_and_sample_std(returns)?;
    Ok(signed_ratio(mean, std) * (periods_per_year as f64).sqrt())
}

/// Annualized Sortino against a MAR of 0: (mean / downside dev) * sqrt(ppy).
///
/// Downside deviation is a population quantity over EVERY observation:
/// sqrt((1/n) * sum(min(r, 0)^2)) - periods above target contribute zero,
/// they are not dropped.  Implementations disagree on 1/n vs 1/(n-1) and on
/// dropping upside periods; we use the full-sample 1/n form and say so.
pub fn sortino(returns: &[f64], periods_per_year: u32) -> Result<f64, StatsError> {
    if periods_per_year == 0 {
        return Err(StatsError::ZeroPeriods);
    }
    let n = returns.len();
    if n < 2 {
        return Err(StatsError::NotEnoughObservations {
            what: "sortino".to_string(),
            need: 2,
            got: n,
        });
    }
    let mean = fsum(returns.iter().copied()) / n as f64;
    let dd = (fsum(returns.iter().map(|x| {
        let down = x.min(0.0);
        down * down
    })) / n as f64)
        .sqrt();
    Ok(signed_ratio(mean, dd) * (periods_per_year as f64).sqrt())
}

/// How [`max_drawdown`] should read its input.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DdKind {
    /// Guess: any value <= 0, or every |v| < 1, reads as returns; else equity.
    Auto,
    /// Input is an equity curve (wealth levels).
    Equity,
    /// Input is per-period returns (compounded internally).
    Returns,
}

/// Worst peak-to-trough loss as a POSITIVE fraction (0.2 means a 20% dd).
///
/// `values` is either a per-period return stream (compounded internally as
/// cumprod(1 + r)) or an equity curve (wealth levels); [`DdKind::Auto`]
/// guesses between them: any value <= 0, or every value with |v| < 1, reads
/// as returns.  The guess is a documented rule, not mind-reading - an equity
/// account living entirely inside (0, 1) dollars must pass [`DdKind::Equity`]
/// explicitly.
///
/// Returns 0.0 for empty or single-point input (no drawdown is possible).
/// A leveraged wipeout can push the value past 1.0; that is the arithmetic
/// being honest, not a bug.
pub fn max_drawdown(values: &[f64], kind: DdKind) -> f64 {
    let kind = match kind {
        DdKind::Equity => DdKind::Equity,
        DdKind::Returns => DdKind::Returns,
        DdKind::Auto => {
            let looks_like_returns =
                values.iter().any(|v| *v <= 0.0) || values.iter().all(|v| v.abs() < 1.0);
            if looks_like_returns {
                DdKind::Returns
            } else {
                DdKind::Equity
            }
        }
    };
    if values.len() < 2 {
        return 0.0;
    }
    let levels: Vec<f64> = match kind {
        DdKind::Returns => {
            let mut out = Vec::with_capacity(values.len());
            let mut equity = 1.0;
            for r in values {
                equity *= 1.0 + r;
                out.push(equity);
            }
            out
        }
        _ => values.to_vec(),
    };
    let mut peak = f64::NEG_INFINITY;
    let mut worst: f64 = 0.0;
    for level in levels {
        if level > peak {
            peak = level;
        }
        if peak > 0.0 {
            worst = worst.max(1.0 - level / peak);
        }
    }
    worst
}

/// Calmar: CAGR / max drawdown, both computed from the same stream.
///
/// CAGR = (prod(1 + r)) ** (periods_per_year / n) - 1.  Assumption: input is
/// per-period RETURNS, not equity levels - calmar has no auto-guessing.
///
/// A stream with no drawdown settles by the sign of its CAGR (inf for a
/// gain, 0.0 for flat).  A total wipeout (some 1 + r <= 0) has no
/// logarithmic CAGR; it is scored as CAGR = -1.0 (everything lost is
/// everything lost, whatever the path).
pub fn calmar(returns: &[f64], periods_per_year: u32) -> Result<f64, StatsError> {
    if periods_per_year == 0 {
        return Err(StatsError::ZeroPeriods);
    }
    let n = returns.len();
    if n < 1 {
        return Err(StatsError::EmptyStream {
            what: "calmar".to_string(),
        });
    }
    let growth = compound_growth(returns);
    let cagr = if growth <= 0.0 {
        -1.0
    } else {
        growth.powf(periods_per_year as f64 / n as f64) - 1.0
    };
    let dd = max_drawdown(returns, DdKind::Returns);
    if dd > 0.0 {
        Ok(cagr / dd)
    } else if cagr > 0.0 {
        Ok(f64::INFINITY)
    } else {
        Ok(0.0)
    }
}

/// prod(1 + r) - the compounded growth factor of a return stream.
fn compound_growth(returns: &[f64]) -> f64 {
    let mut equity = 1.0;
    for r in returns {
        equity *= 1.0 + r;
    }
    equity
}

/// t-statistic of the mean: mean / (sample std / sqrt(n)).
///
/// The naive question behind every Sharpe: how many standard errors is the
/// average return away from zero?  Two-sided p-values are the caller's
/// business; this hands back the raw statistic.
pub fn t_stat(returns: &[f64]) -> Result<f64, StatsError> {
    let (mean, std) = mean_and_sample_std(returns)?;
    Ok(signed_ratio(mean, std) * (returns.len() as f64).sqrt())
}

/// (mean, std, skew, kurtosis) as POPULATION moments over the sample.
///
/// Population (1/n) throughout: these describe the SHAPE of the stream you
/// actually observed, not an estimate of some superpopulation.  Kurtosis is
/// Pearson's (a normal stream scores 3.0), not the excess form.  A stream
/// with no dispersion has no shape to speak of either: std 0.0, skew 0.0,
/// kurtosis 3.0 - stated so callers never have to guess.
pub fn moments(values: &[f64]) -> Result<(f64, f64, f64, f64), StatsError> {
    let n = values.len();
    if n == 0 {
        return Err(StatsError::EmptyStream {
            what: "moments".to_string(),
        });
    }
    let mean = fsum(values.iter().copied()) / n as f64;
    if n == 1 {
        return Ok((mean, 0.0, 0.0, 3.0));
    }
    let m2 = fsum(values.iter().map(|x| (x - mean) * (x - mean))) / n as f64;
    if m2 <= 0.0 {
        return Ok((mean, 0.0, 0.0, 3.0));
    }
    let std = m2.sqrt();
    let m3 = fsum(values.iter().map(|x| {
        let d = x - mean;
        d * d * d
    })) / n as f64;
    let m4 = fsum(values.iter().map(|x| {
        let d = x - mean;
        let d2 = d * d;
        d2 * d2
    })) / n as f64;
    let skew = m3 / (std * std * std);
    let kurtosis = m4 / (m2 * m2);
    Ok((mean, std, skew, kurtosis))
}

// ---------------------------------------------------------------------------
// the normal curve: erfc by hand, no libm crate.
// ---------------------------------------------------------------------------

/// erf(x) for 0 <= x <= 2, via the alternating Taylor series with
/// compensated summation.  Terms peak near n = x^2 (max magnitude < 3), so
/// the compensated sum stays good to a few ulps across the branch.
fn erf_series(x: f64) -> f64 {
    let two_over_sqrt_pi = 2.0 / std::f64::consts::PI.sqrt();
    let x2 = x * x;
    let mut terms = Vec::with_capacity(64);
    let mut term = two_over_sqrt_pi * x; // the n = 0 term
    terms.push(term);
    let mut n = 1u32;
    // t_n = t_{n-1} * (-x^2) * (2n-1) / (n * (2n+1))
    while n < 400 {
        let nf = f64::from(n);
        term *= -x2 * (2.0 * nf - 1.0) / (nf * (2.0 * nf + 1.0));
        terms.push(term);
        if term.abs() < 1e-18 {
            break;
        }
        n += 1;
    }
    fsum(terms)
}

/// erfc(x) for x >= 2 via the continued fraction (DLMF 7.9.1), modified
/// Lentz:
///
/// erfc(x) = exp(-x^2) / (sqrt(pi) * (x + (1/2)/(x + 1/(x + (3/2)/(x + ...)))))
///
/// with all b_k = x and a_k = k/2.  Convergence accelerates with x; the
/// iteration cap of 400 is orders of magnitude past where the delta test
/// fires for x >= 2.
fn erfc_continued_fraction(x: f64) -> f64 {
    let tiny = 1e-300;
    let mut f = x;
    let mut c = f;
    let mut d = 0.0;
    let mut a = 0.5; // a_1
    for _ in 0..400u32 {
        d = x + a * d;
        if d == 0.0 {
            d = tiny;
        }
        d = 1.0 / d;
        c = x + a / c;
        if c == 0.0 {
            c = tiny;
        }
        let delta = c * d;
        f *= delta;
        if (delta - 1.0).abs() < f64::EPSILON {
            break;
        }
        a += 0.5;
    }
    (-x * x).exp() / (std::f64::consts::PI.sqrt() * f)
}

/// The complementary error function, std-only.
///
/// Branches, documented: series for 0 <= x < 2 (erfc = 1 - erf, which loses
/// at most ~7 significant digits of erfc near x = 2 - still far past the
/// engine's 1e-9 promises), continued fraction for x >= 2 (no cancellation
/// in the tail), and erfc(-x) = 2 - erfc(x) by symmetry.  exp(-x^2)
/// underflows to 0 past x ~ 27; erfc goes with it - the double range, not a
/// bug.
pub fn erfc(x: f64) -> f64 {
    if x.is_nan() {
        return f64::NAN;
    }
    if x < 0.0 {
        return 2.0 - erfc(-x);
    }
    if x < 2.0 {
        1.0 - erf_series(x)
    } else {
        erfc_continued_fraction(x)
    }
}

/// Standard normal CDF: Phi(x) = 0.5 * erfc(-x / sqrt(2)).
pub fn normal_cdf(x: f64) -> f64 {
    0.5 * erfc(-x / std::f64::consts::SQRT_2)
}

// Acklam's rational approximation coefficients (Peter Acklam's inverse
// normal CDF, the widely reproduced double-precision set; |rel err| <=
// 1.15e-9 before refinement).
const ACKLAM_A: [f64; 6] = [
    -3.969_683_028_665_376e+01,
    2.209_460_984_245_205e+02,
    -2.759_285_104_469_687e+02,
    1.383_577_518_672_69e2,
    -3.066_479_806_614_716e+01,
    2.506_628_277_459_239e+00,
];
const ACKLAM_B: [f64; 5] = [
    -5.447_609_879_822_406e+01,
    1.615_858_368_580_409e+02,
    -1.556_989_798_598_866e+02,
    6.680_131_188_771_972e+01,
    -1.328_068_155_288_572e+01,
];
const ACKLAM_C: [f64; 6] = [
    -7.784_894_002_430_293e-03,
    -3.223_964_580_411_365e-01,
    -2.400_758_277_161_838e+00,
    -2.549_732_539_343_734e+00,
    4.374_664_141_464_968e+00,
    2.938_163_982_698_783e+00,
];
const ACKLAM_D: [f64; 4] = [
    7.784_695_709_041_462e-03,
    3.224_671_290_700_398e-01,
    2.445_134_137_142_996e+00,
    3.754_408_661_907_416e+00,
];
const ACKLAM_P_LOW: f64 = 0.024_25;

/// Inverse standard normal CDF (quantile), Acklam's algorithm plus one
/// Halley refinement step against [`normal_cdf`].
///
/// Accuracy, documented: the rational core is ~1.15e-9 relative; the Halley
/// step (which needs only a good Phi - hence the careful erfc) pulls the
/// central and moderately-tail region to near machine precision, ~1e-15.
/// Deep-tail inputs (p below ~1e-300) run out of double range; they return
/// finite answers whose precision degrades gracefully rather than NaN.
pub fn inv_normal_cdf(p: f64) -> Result<f64, StatsError> {
    if !(p > 0.0 && p < 1.0) {
        return Err(StatsError::BadProbability(p));
    }
    let x = if p < ACKLAM_P_LOW {
        let q = (-2.0 * p.ln()).sqrt();
        (((((ACKLAM_C[0] * q + ACKLAM_C[1]) * q + ACKLAM_C[2]) * q + ACKLAM_C[3]) * q
            + ACKLAM_C[4])
            * q
            + ACKLAM_C[5])
            / ((((ACKLAM_D[0] * q + ACKLAM_D[1]) * q + ACKLAM_D[2]) * q + ACKLAM_D[3]) * q + 1.0)
    } else if p <= 1.0 - ACKLAM_P_LOW {
        // Central region: q = p - 0.5; x = P(r)*q / Q(r) with r = q*q,
        // where P has the six A coefficients and Q the five B coefficients
        // (the innermost polynomial, then one more *r, then +1).
        let q = p - 0.5;
        let r = q * q;
        let num = (((((ACKLAM_A[0] * r + ACKLAM_A[1]) * r + ACKLAM_A[2]) * r + ACKLAM_A[3]) * r
            + ACKLAM_A[4])
            * r
            + ACKLAM_A[5])
            * q;
        let den = ((((ACKLAM_B[0] * r + ACKLAM_B[1]) * r + ACKLAM_B[2]) * r + ACKLAM_B[3]) * r
            + ACKLAM_B[4])
            * r
            + 1.0;
        num / den
    } else {
        let q = (-2.0 * (1.0 - p).ln()).sqrt();
        -(((((ACKLAM_C[0] * q + ACKLAM_C[1]) * q + ACKLAM_C[2]) * q + ACKLAM_C[3]) * q
            + ACKLAM_C[4])
            * q
            + ACKLAM_C[5])
            / ((((ACKLAM_D[0] * q + ACKLAM_D[1]) * q + ACKLAM_D[2]) * q + ACKLAM_D[3]) * q + 1.0)
    };
    // One Halley step: Newton-with-extra-order, from Acklam's own notes.
    let e = 0.5 * erfc(-x / std::f64::consts::SQRT_2) - p;
    let u = e * std::f64::consts::TAU.sqrt() * (x * x / 2.0).exp();
    Ok(x - u / (1.0 + x * u / 2.0))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn close(a: f64, b: f64, tol: f64) -> bool {
        (a - b).abs() <= tol * a.abs().max(b.abs()).max(1.0)
    }

    #[test]
    fn sharpe_hand_built_cases() {
        // Odd stream, arithmetic a reader can redo by hand:
        // mean = 0.012; squared devs sum 2.68e-3; sample var = 6.7e-4.
        let s = [0.03, -0.01, 0.02, -0.02, 0.04];
        let (mean, std) = mean_and_sample_std(&s).expect("n >= 2");
        assert!((mean - 0.012).abs() < 1e-15);
        assert!((std - 0.025_884_358_211_096_042).abs() < 1e-12);
        let sr = sharpe(&s, 252).expect("ppy > 0");
        assert!((sr - 7.359_428_920_089_572).abs() < 1e-9);
        // Degenerate streams settle by the sign of the mean.
        assert_eq!(sharpe(&[0.01; 10], 252).expect("ok"), f64::INFINITY);
        assert_eq!(sharpe(&[-0.01; 10], 252).expect("ok"), f64::NEG_INFINITY);
        let alternating = [0.01, -0.01, 0.01, -0.01];
        assert_eq!(sharpe(&alternating, 252).expect("ok"), 0.0);
        assert!(sharpe(&[0.01], 252).is_err());
        assert!(sharpe(&s, 0).is_err());
    }

    #[test]
    fn drawdown_and_calmar_hand_cases() {
        // equity 1.1 -> 0.88 -> 0.968: drawdown exactly 0.2.
        let dd = max_drawdown(&[0.1, -0.2, 0.1], DdKind::Returns);
        assert!((dd - 0.2).abs() < 1e-9, "dd {dd}");
        assert_eq!(max_drawdown(&[1.0], DdKind::Auto), 0.0);
        // Equity levels pass through untouched when told so.
        let dd = max_drawdown(&[100.0, 80.0, 96.0], DdKind::Equity);
        assert!((dd - 0.2).abs() < 1e-9);
        // A wipeout scores CAGR = -1.0 and can push Calmar negative.
        let c = calmar(&[0.5, -0.9], 252).expect("ok");
        assert!(c.is_finite() && c < 0.0);
    }

    #[test]
    fn moments_match_the_reference_implementation() {
        // These decimals were computed with the Python package's
        // deflate.moments (same 1/n, Pearson conventions) - the two engines
        // must agree to full displayed precision.
        let s = [0.03, -0.01, 0.02, -0.02, 0.04];
        let (mean, std, skew, kurt) = moments(&s).expect("non-empty");
        assert!((mean - 0.012).abs() < 1e-15);
        assert!((std - 0.023_151_673_805_580_45).abs() < 1e-15);
        assert!((skew - (-0.243_688_257_365_488_16)).abs() < 1e-12);
        assert!((kurt - 1.396_858_988_638_895_1).abs() < 1e-12);
        // Degenerate: no dispersion, no shape, and we say so.
        assert_eq!(moments(&[4.2]).expect("one point"), (4.2, 0.0, 0.0, 3.0));
        assert_eq!(
            moments(&[1.0, 1.0, 1.0]).expect("flat"),
            (1.0, 0.0, 0.0, 3.0)
        );
        assert!(moments(&[]).is_err());
        // A hand-built asymmetric stream has positive skew, sign you can see.
        let (_, _, skew, _) = moments(&[0.0, 0.0, 0.0, 0.0, 10.0]).expect("ok");
        assert!(skew > 1.0);
    }

    #[test]
    fn erfc_matches_known_constants() {
        // From Python's math.erfc (double precision), tolerance 1e-12
        // relative - the two implementations are independent.
        assert!(close(erfc(0.0), 1.0, 1e-15));
        assert!(close(erfc(0.5), 0.479_500_122_186_953_5, 1e-12));
        assert!(close(erfc(1.0), 0.157_299_207_050_285_13, 1e-12));
        assert!(close(erfc(2.0), 0.004_677_734_981_047_265, 1e-11));
        assert!(close(erfc(3.0), 2.209_049_699_858_543_8e-5, 1e-11));
        assert!(close(erfc(5.0), 1.537_459_794_428_035_1e-12, 1e-9));
        // Symmetry is the identity the tail branch leans on.
        assert!(close(erfc(-1.0), 2.0 - erfc(1.0), 1e-15));
        // erf itself: erf(1) = 1 - erfc(1).
        assert!(close(erfc(1.0) + erf_series(1.0), 1.0, 1e-12));
    }

    #[test]
    fn normal_cdf_matches_known_constants() {
        assert!(close(normal_cdf(0.0), 0.5, 1e-15));
        assert!(close(normal_cdf(1.0), 0.841_344_746_068_542_9, 1e-12));
        assert!(close(normal_cdf(2.0), 0.977_249_868_051_820_8, 1e-12));
        assert!(close(normal_cdf(3.0), 0.998_650_101_968_369_9, 1e-12));
        assert!(close(normal_cdf(-1.0), 0.158_655_253_931_457_07, 1e-12));
        // The classic two-sided 95% quantile (the constant is quoted to 6
        // decimals, so the tolerance admits the rounding).
        assert!(close(normal_cdf(1.959_964), 0.975, 1e-8));
        assert!(close(normal_cdf(1.959_963_984_540_054), 0.975, 1e-13));
    }

    #[test]
    fn inv_cdf_roundtrips_and_hits_known_quantiles() {
        assert!(close(
            inv_normal_cdf(0.975).expect("ok"),
            1.959_963_984_540_053_6,
            1e-12
        ));
        assert!(close(
            inv_normal_cdf(0.01).expect("ok"),
            -2.326_347_874_040_840_8,
            1e-12
        ));
        assert!(close(inv_normal_cdf(0.5).expect("ok"), 0.0, 1e-15));
        assert!(close(
            inv_normal_cdf(1e-10).expect("ok"),
            -6.361_340_902_404_056,
            1e-9
        ));
        // Roundtrip across the whole (0,1) line: |Phi(inv(p)) - p| tiny.
        for i in 1..1000u32 {
            let p = f64::from(i) / 1000.0;
            let x = inv_normal_cdf(p).expect("ok");
            assert!(
                (normal_cdf(x) - p).abs() < 1e-12,
                "roundtrip failed at p={p}"
            );
        }
        // And in the deep tails where the rational branch works alone.
        for &p in &[1e-12, 1e-20, 1e-50, 1e-100, 1.0 - 1e-12] {
            let x = inv_normal_cdf(p).expect("ok");
            let back = normal_cdf(x);
            assert!(
                (back - p).abs() <= 1e-10 * p.abs().max(1e-300),
                "deep-tail roundtrip at p={p}: back={back}"
            );
        }
        assert!(inv_normal_cdf(0.0).is_err());
        assert!(inv_normal_cdf(1.0).is_err());
        assert!(inv_normal_cdf(-0.5).is_err());
    }

    #[test]
    fn sortino_and_t_stat_conventions() {
        let s = [0.03, -0.01, 0.02, -0.02, 0.04];
        // Reference decimals from the Python package (same 1/n downside
        // convention, same annualization).
        assert!((sortino(&s, 252).expect("ok") - 19.049_409_439_665_05).abs() < 1e-9);
        assert!((t_stat(&s).expect("ok") - 1.036_642_110_697_632_2).abs() < 1e-12);
        // Downside-only stream: upside periods contribute zero, not dropped.
        let dd_stream = [-0.01, 0.02, -0.01, 0.02];
        let (mean, _) = mean_and_sample_std(&dd_stream).expect("ok");
        let dd = (fsum(dd_stream.iter().map(|x| {
            let d = x.min(0.0);
            d * d
        })) / 4.0)
            .sqrt();
        let expected = signed_ratio(mean, dd) * 252f64.sqrt();
        assert!((sortino(&dd_stream, 252).expect("ok") - expected).abs() < 1e-12);
    }
}
