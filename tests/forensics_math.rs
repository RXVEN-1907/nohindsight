//! The forensics math, pinned two ways: against decimals computed with the
//! Python package (the same formulas, an independent implementation), and
//! against arithmetic the tests redo locally.

use nh::forensics::{
    audit, deflated_psr, expected_max_sharpe, hurdle_from_trial_count, permutation_pvalue, psr,
    AuditConfig, Level,
};
use nh::pbo::{overfit_report, pbo, PboConfig};
use nh::rng::SplitMix64;
use nh::stats::{max_drawdown, moments, sharpe, DdKind};

/// The worked example, as decimals: the stream's moments, Sharpe and PSR
/// were computed with the Python package's deflate module - same
/// conventions (population moments, annualized SR inside the variance
/// term, ddof=1 Sharpe), independent code.
const STREAM: [f64; 5] = [0.03, -0.01, 0.02, -0.02, 0.04];

#[test]
fn psr_matches_the_hand_pinned_worked_example() {
    // Python: deflate.psr(stream, 1.0, 252) = 0.9999957181354922
    let psr_bench1 = psr(&STREAM, 1.0, 252).expect("ok");
    assert!(
        (psr_bench1 - 0.999_995_718_135_492_2).abs() < 1e-9,
        "psr {psr_bench1}"
    );
    // And against a benchmark of zero: 0.9999998700506154
    let psr_bench0 = psr(&STREAM, 0.0, 252).expect("ok");
    assert!(
        (psr_bench0 - 0.999_999_870_050_615_4).abs() < 1e-9,
        "psr {psr_bench0}"
    );
    // The z-score inside is reproducible from first principles in THIS
    // file: annualized SR = 7.359428920089572, n-1 = 4, skew/kurtosis from
    // the same population moments.
    let (mean, std_pop, skew, kurt) = moments(&STREAM).expect("ok");
    assert!((mean - 0.012).abs() < 1e-15);
    assert!((std_pop - 0.023_151_673_805_580_45).abs() < 1e-15);
    let sr_ann = sharpe(&STREAM, 252).expect("ok");
    assert!((sr_ann - 7.359_428_920_089_572).abs() < 1e-9);
    let var_term = 1.0 - skew * sr_ann + ((kurt - 1.0) / 4.0) * sr_ann * sr_ann;
    let z1 = (sr_ann - 1.0) * (4.0f64).sqrt() / var_term.sqrt();
    let z0 = (sr_ann - 0.0) * (4.0f64).sqrt() / var_term.sqrt();
    // Phi(z) through the crate's normal CDF must equal each psr answer to
    // a rounding error - the exact decimals above are what keeps this
    // honest (the crate's Phi is the same call site psr uses).
    assert!((nh::stats::normal_cdf(z1) - psr_bench1).abs() < 1e-12);
    assert!((nh::stats::normal_cdf(z0) - psr_bench0).abs() < 1e-12);
}

#[test]
fn dsr_is_monotone_in_trials_and_matches_the_reference() {
    // d(1), d(5) pinned from the Python package.
    assert!(
        (deflated_psr(&STREAM, 1, None, 252).expect("ok") - 0.999_999_870_050_615_4).abs() < 1e-9
    );
    assert!(
        (deflated_psr(&STREAM, 5, None, 252).expect("ok") - 0.999_992_048_649_743_6).abs() < 1e-9
    );
    // Monotone: the more trials you admit to, the harder the bar.
    let mut prev = 1.0;
    for trials in [1usize, 2, 5, 10, 40, 200] {
        let d = deflated_psr(&STREAM, trials, None, 252).expect("ok");
        assert!(
            d <= prev + 1e-12,
            "dsr must not rise with trials at {trials}"
        );
        prev = d;
    }
    // The count-only hurdle itself, pinned: hurdle(5) = 1.192594001014789.
    assert!((hurdle_from_trial_count(5).expect("ok") - 1.192_594_001_014_789).abs() < 1e-9);
}

#[test]
fn expected_max_sharpe_grows_with_trials_and_variance() {
    // Pinned: expected_max_sharpe([1.0, 1.2, 0.8, 1.5, 0.9])
    //      = 0.29599404689520037
    let trials = [1.0, 1.2, 0.8, 1.5, 0.9];
    let got = expected_max_sharpe(&trials).expect("ok");
    assert!((got - 0.295_994_046_895_200_37).abs() < 1e-9);
    // Grows with N at a fixed cross-trial spread.
    let spread = |n: usize| {
        let xs: Vec<f64> = (0..n)
            .map(|i| if i % 2 == 0 { 0.5 } else { -0.5 })
            .collect();
        expected_max_sharpe(&xs).expect("ok")
    };
    assert!(spread(10) < spread(100) && spread(100) < spread(1000));
    // Grows with cross-trial variance at fixed N.
    let narrow = expected_max_sharpe(&[0.1, -0.1, 0.1, -0.1]).expect("ok");
    let wide = expected_max_sharpe(&[1.0, -1.0, 1.0, -1.0]).expect("ok");
    assert!(narrow < wide);
    // Degenerate cases, as documented.
    assert_eq!(expected_max_sharpe(&[0.77]).expect("ok"), 0.77);
    assert_eq!(expected_max_sharpe(&[0.9, 0.9]).expect("ok"), 0.9);
    assert!(expected_max_sharpe(&[]).is_err());
}

/// A seeded all-noise matrix: 320 rows x 8 configs, each an iid N(0, 0.01)
/// stream from one splitmix64 - no config has an edge, so the in-sample
/// winner is a coin flip out-of-sample.
fn noise_matrix(rows: usize, configs: usize, seed: u64) -> Vec<Vec<f64>> {
    let mut rng = SplitMix64::new(seed);
    (0..rows)
        .map(|_| (0..configs).map(|_| 0.01 * rng.gauss(0.0, 1.0)).collect())
        .collect()
}

#[test]
fn pbo_is_in_unit_interval_and_high_for_all_noise() {
    let matrix = noise_matrix(320, 8, 7);
    // blocks=8 -> C(8,4) = 70 <= 512: full enumeration, deterministic.
    let value = pbo(
        &matrix,
        PboConfig {
            blocks: 8,
            ..PboConfig::default()
        },
    )
    .expect("ok");
    assert!((0.0..=1.0).contains(&value));
    // All-noise: the IS winner should finish in the worse OOS half about
    // half the time.  Assert the band, not the noise: [0.25, 0.8].
    assert!(
        (0.25..=0.8).contains(&value),
        "all-noise PBO should be highish, got {value}"
    );
    let report = overfit_report(
        &matrix,
        PboConfig {
            blocks: 8,
            ..PboConfig::default()
        },
    )
    .expect("ok");
    assert_eq!(report.pbo, value);
    assert_eq!(report.n_combos, 70);
    // Median rank near the middle for pure noise.
    assert!(
        (1.0..=6.0).contains(&report.is_best_oos_median_rank),
        "median rank {} of 0..7",
        report.is_best_oos_median_rank
    );
}

#[test]
fn pbo_is_low_for_one_clearly_best_config() {
    // Config 0 has a real drift; the rest are the same noise as before.
    let mut matrix = noise_matrix(320, 8, 7);
    for row in matrix.iter_mut() {
        row[0] += 0.01; // a fat, persistent edge on top of the noise
    }
    let value = pbo(
        &matrix,
        PboConfig {
            blocks: 8,
            ..PboConfig::default()
        },
    )
    .expect("ok");
    assert!(value < 0.1, "a real edge keeps winning OOS, got {value}");
    // And the sampling path (more blocks than the cap) stays in range.
    let sampled = pbo(
        &matrix,
        PboConfig {
            blocks: 16,
            ..PboConfig::default()
        },
    )
    .expect("ok");
    assert!(sampled < 0.15, "sampled CSCV agrees, got {sampled}");
}

#[test]
fn pbo_ties_favor_the_audited_config() {
    // Two identical columns: every rank is 0 under the documented rule
    // (strictly-higher count), so PBO is exactly 0.
    let matrix = vec![vec![0.01, 0.01]; 40];
    let value = pbo(
        &matrix,
        PboConfig {
            blocks: 4,
            ..PboConfig::default()
        },
    )
    .expect("ok");
    assert_eq!(value, 0.0);
}

#[test]
fn permutation_pvalue_pins_the_null_and_the_edge() {
    // Strong drift: essentially no sign-flipped no-edge path matches it.
    let drift: Vec<f64> = (0..500)
        .map(|i| if i % 2 == 0 { 0.02 } else { -0.004 })
        .collect();
    let observed = sharpe(&drift, 252).expect("ok");
    let p = permutation_pvalue(&drift, observed, 1000, 252, 0).expect("ok");
    assert!(p <= 0.01, "drift p {p}");
    // Exact zero-mean noise: the observed Sharpe is everyday luck.
    let noise: Vec<f64> = (0..500)
        .map(|i| if i % 2 == 0 { 0.01 } else { -0.01 })
        .collect();
    let observed = sharpe(&noise, 252).expect("ok");
    let p = permutation_pvalue(&noise, observed, 1000, 252, 0).expect("ok");
    assert!(p > 0.3, "noise p {p}");
    // Deterministic: same seed, same fraction.
    let again = permutation_pvalue(&noise, observed, 1000, 252, 0).expect("ok");
    assert_eq!(p, again);
}

#[test]
fn drawdown_math_on_a_hand_built_series() {
    // equity 1.10 -> 0.88 -> 0.968: the worst peak-to-trough is 0.20.
    let dd = max_drawdown(&[0.1, -0.2, 0.1], DdKind::Returns);
    assert!((dd - 0.2).abs() < 1e-9, "dd {dd}");
    // Equity levels are used as-is when told so.
    let dd = max_drawdown(&[1_000_000.0, 800_000.0, 960_000.0], DdKind::Equity);
    assert!((dd - 0.2).abs() < 1e-9);
    assert_eq!(max_drawdown(&[1.0], DdKind::Auto), 0.0);
    assert_eq!(max_drawdown(&[], DdKind::Auto), 0.0);
}

#[test]
fn audit_levels_across_crafted_inputs() {
    // Strong single-trial drift: REAL, with a closing reason that says so.
    let drift: Vec<f64> = (0..600)
        .map(|i| if i % 2 == 0 { 0.015 } else { -0.005 })
        .collect();
    let verdict = audit(
        &drift,
        AuditConfig {
            mc_paths: 400,
            ..AuditConfig::default()
        },
        None,
    )
    .expect("ok");
    assert_eq!(verdict.level, Level::Real);
    assert!(verdict.reasons.iter().any(|r| r.contains("real edge")));
    assert_eq!(verdict.numbers.n_obs, 600);

    // Exact zero-mean noise: huge p -> FANTASY, and the reasons say why.
    let noise: Vec<f64> = (0..600)
        .map(|i| if i % 2 == 0 { 0.01 } else { -0.01 })
        .collect();
    let verdict = audit(
        &noise,
        AuditConfig {
            mc_paths: 400,
            ..AuditConfig::default()
        },
        None,
    )
    .expect("ok");
    assert_eq!(verdict.level, Level::Fantasy);
    assert!(verdict
        .reasons
        .iter()
        .any(|r| r.contains("indistinguishable from luck")));
    // The three forensic-test reasons cite their numbers; the closing
    // reason is the verdict's plain sentence and needs none.
    for reason in verdict.reasons.iter().take(3) {
        assert!(reason.chars().any(|c| c.is_ascii_digit()), "{reason}");
    }
    // Refusals: too few observations, zero trials.
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

#[test]
fn audit_includes_pbo_when_a_matrix_is_supplied() {
    // A family whose audited member has the drift pattern and whose other
    // members are noise: the audited config keeps its edge OOS, so PBO is
    // low and REAL survives the whole panel.
    let drift: Vec<f64> = (0..320)
        .map(|i| if i % 2 == 0 { 0.015 } else { -0.005 })
        .collect();
    let mut matrix = noise_matrix(320, 4, 11);
    for (t, row) in matrix.iter_mut().enumerate() {
        row[0] = drift[t];
    }
    let verdict = audit(
        &drift,
        AuditConfig {
            mc_paths: 200,
            ..AuditConfig::default()
        },
        Some(&matrix),
    )
    .expect("ok");
    let pbo_value = verdict.numbers.pbo.expect("matrix was supplied");
    assert!((0.0..=1.0).contains(&pbo_value));
    assert!(verdict.numbers.n_combos.expect("combos reported") > 0);
    assert!(verdict
        .reasons
        .iter()
        .any(|r| r.contains("train/test splits")));
}
