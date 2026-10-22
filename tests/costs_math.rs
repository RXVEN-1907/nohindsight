//! The reality tax, itemized and pinned: fee schedules, the half-spread
//! convention, square-root impact scaling, borrow proration, composition,
//! and the fantasy-vs-real shrinkage.

use nh::costs::{
    fantasy_vs_real, BorrowModel, CostModel, CostsError, FeeSchedule, ImpactKind, ImpactModel,
    Side, SpreadModel, Trade,
};

fn trade(side: Side, price: f64, qty: f64) -> Trade {
    Trade::new(
        "2024-01-02T15:00:00+00:00",
        "TEST",
        side,
        price,
        qty,
        Some(200.0),    // sigma_bps
        Some(10_000.0), // adv
        0.0,
    )
    .expect("valid trade")
}

#[test]
fn fee_schedules_add_both_lines() {
    // flat_bps(10) on $1M -> $1000; per_share(0.5) on 1000 shares -> $5.00.
    assert!((FeeSchedule::flat_bps(10.0).apply(1_000_000.0, 0.0) - 1000.0).abs() < 1e-9);
    assert!((FeeSchedule::per_share(0.5).apply(0.0, 1000.0) - 5.0).abs() < 1e-12);
    // Both lines add: a broker charging bps AND per-share is expressible.
    let both = FeeSchedule {
        bps: 10.0,
        cents_per_share: 0.5,
    };
    assert!((both.apply(1_000_000.0, 1000.0) - 1005.0).abs() < 1e-9);
    // zero() is the free lunch, and the composition default.
    assert_eq!(FeeSchedule::zero().apply(1e9, 1e6), 0.0);
}

#[test]
fn half_spread_convention_is_half_per_crossing() {
    let spread = SpreadModel::constant(20.0);
    // The worked example from the module docs: constant(20), price=100,
    // qty=10 -> half-spread is 10bp of $1,000 -> $1.00 for ONE crossing.
    let one = spread.half_cost(100.0, 10.0, None);
    assert!((one - 1.0).abs() < 1e-12);
    // A round trip is TWO Trade records: exactly the full spread, once.
    let round_trip = one * 2.0;
    assert!((round_trip - 100.0 * 10.0 * 20.0 / 1e4).abs() < 1e-12);
    // Volatility scaling: widens only when sigma rides along; an
    // unestimated sigma is not a calm market, but we cannot charge for
    // fear we cannot see.
    let scaled = SpreadModel::volatility_scaled(20.0, 8.0);
    assert_eq!(scaled.spread_bps(None), 20.0);
    let expected = 20.0 * (1.0 + 8.0 * 100.0 / 1000.0);
    assert!((scaled.spread_bps(Some(100.0)) - expected).abs() < 1e-12);
}

#[test]
fn sqrt_impact_scales_as_the_square_root() {
    let impact = ImpactModel::sqrt_law(1.0);
    assert_eq!(impact.kind, ImpactKind::Sqrt);
    // impact_bps = coeff * sigma_bps * sqrt(qty / adv), exactly.
    let base = impact.estimate(100.0, Some(10_000.0), Some(200.0));
    let expected = 200.0f64 * (100.0f64 / 10_000.0).sqrt();
    assert_eq!(base, (expected, true));
    // Quadruple the size -> double the impact (Bouchaud / Almgren-Chriss).
    let big = impact.estimate(400.0, Some(10_000.0), Some(200.0));
    assert!((big.0 - 2.0 * base.0).abs() < 1e-12);
    // Missing ADV or sigma: zero, but NOT explained - the flag exists so
    // reports say "impact unestimated", not "0".
    assert_eq!(impact.estimate(100.0, None, Some(200.0)), (0.0, false));
    assert_eq!(impact.estimate(100.0, Some(10_000.0), None), (0.0, false));
    assert_eq!(impact.estimate(100.0, Some(0.0), Some(200.0)), (0.0, false));
    // qty == 0 with full inputs is a real zero, not a missing estimate.
    assert_eq!(
        impact.estimate(0.0, Some(10_000.0), Some(200.0)),
        (0.0, true)
    );
    // The linear alternative is participation-linear, sigma-independent.
    let linear = ImpactModel::linear(0.1);
    let (bps, explained) = linear.estimate(250.0, Some(10_000.0), None);
    assert!((bps - 0.0025).abs() < 1e-15);
    assert!(explained);
    // Convenience form discards the flag.
    assert!((impact.impact_bps(100.0, Some(10_000.0), Some(200.0)) - expected).abs() < 1e-15);
}

#[test]
fn borrow_prorates_by_calendar_days() {
    let borrow = BorrowModel::annual(0.05);
    // 5% of $100k for 36.5 days = $500.
    assert!((borrow.cost(100_000.0, 36.5) - 500.0).abs() < 1e-9);
    assert_eq!(borrow.cost(1e9, 0.0), 0.0);
}

#[test]
fn composition_sums_the_four_lines() {
    let model = CostModel {
        fees: FeeSchedule::flat_bps(10.0),
        spread: SpreadModel::constant(20.0),
        impact: Some(ImpactModel::sqrt_law(1.0)),
        borrow: Some(BorrowModel::annual(0.05)),
    };
    let t = Trade::new(
        "2024-01-02T15:00:00+00:00",
        "TEST",
        Side::Sell,
        100.0,
        10.0,
        Some(200.0),
        Some(10_000.0),
        36.5,
    )
    .expect("valid");
    let b = model.price(&t);
    let impact_bps = 200.0f64 * (10.0f64 / 10_000.0).sqrt();
    let notional = 100.0 * 10.0;
    let expected = notional * 10.0 / 1e4          // fee
        + notional * 10.0 / 1e4                   // HALF of the 20bp spread
        + notional * impact_bps / 1e4             // impact
        + notional * 0.05 * 36.5 / 365.0; // borrow: sells pay rent
    assert!(
        (b.total() - expected).abs() < 1e-9,
        "{} vs {expected}",
        b.total()
    );
    assert!(b.impact_explained);
    // total_bps is derived from the parts, not stored.
    assert!((b.total_bps() - b.total() / b.notional * 1e4).abs() < 1e-9);
    // Buys pay no borrow.
    let buy = Trade::new(
        "t",
        "TEST",
        Side::Buy,
        100.0,
        10.0,
        Some(200.0),
        Some(10_000.0),
        36.5,
    )
    .expect("valid");
    let bb = model.price(&buy);
    assert_eq!(bb.borrow_cost, 0.0);
}

#[test]
fn total_bps_is_notional_weighted() {
    let model = CostModel {
        fees: FeeSchedule::flat_bps(100.0), // an expensive broker
        ..CostModel::default()
    };
    // 1bp... no: 100bp on $1M plus 100bp on $10 is still 100bp, not 50.5 -
    // weighting by notional, not averaging rates.
    let big = Trade::new("t1", "TEST", Side::Buy, 100.0, 10_000.0, None, None, 0.0).expect("ok");
    let small = Trade::new("t2", "TEST", Side::Buy, 1.0, 10.0, None, None, 0.0).expect("ok");
    let agg = model.total_bps(&[big, small]);
    assert!((agg - 100.0).abs() < 1e-9, "agg {agg}");
    // Zero notional overall: 0.0, not a division by zero.
    assert_eq!(model.total_bps(&[]), 0.0);
    let zero = Trade::new("t3", "TEST", Side::Buy, 50.0, 0.0, None, None, 0.0).expect("ok");
    assert_eq!(model.total_bps(&[zero]), 0.0);
}

#[test]
fn fantasy_vs_real_shrinks_monotonically() {
    let returns: Vec<f64> = (0..250)
        .map(|i| if i % 2 == 0 { 0.004 } else { -0.002 })
        .collect();
    // Zero-cost identity: no drag, shrinkage exactly 1.
    let free = fantasy_vs_real(&returns, 0.0, 252).expect("ok");
    assert!((free.shrinkage - 1.0).abs() < 1e-12);
    assert!(free.drag_per_period.abs() < 1e-15);
    // Monotone in the cost: more bps, less Sharpe after.
    let cheap = fantasy_vs_real(&returns, 1.0, 252).expect("ok");
    let dear = fantasy_vs_real(&returns, 10.0, 252).expect("ok");
    assert!(free.sharpe_after > cheap.sharpe_after);
    assert!(cheap.sharpe_after > dear.sharpe_after);
    assert!(dear.shrinkage < cheap.shrinkage && cheap.shrinkage < 1.0);
    assert!((dear.drag_per_period - 10.0 / 1e4).abs() < 1e-15);
    assert_eq!(dear.n_periods, 250);
    // Losing strategies get hurt too - shrinkage above 1.0, honestly.
    let losing: Vec<f64> = (0..250)
        .map(|i| if i % 2 == 0 { -0.004 } else { 0.002 })
        .collect();
    let hurt = fantasy_vs_real(&losing, 5.0, 252).expect("ok");
    assert!(hurt.shrinkage > 1.0);
    // Fewer observations than a Sharpe needs: refused.
    assert!(fantasy_vs_real(&[0.01], 1.0, 252).is_err());
}

#[test]
fn trade_validation_refuses_nonsense() {
    assert!(matches!(
        Trade::new("t", "", Side::Buy, 10.0, 1.0, None, None, 0.0),
        Err(CostsError::EmptySymbol)
    ));
    assert!(matches!(
        Trade::new("t", "S", Side::Buy, 0.0, 1.0, None, None, 0.0),
        Err(CostsError::NonPositivePrice(_))
    ));
    assert!(matches!(
        Trade::new("t", "S", Side::Buy, 10.0, -1.0, None, None, 0.0),
        Err(CostsError::NegativeQty(_))
    ));
    assert!(matches!(
        Trade::new("t", "S", Side::Buy, 10.0, 1.0, None, None, -1.0),
        Err(CostsError::NegativeHoldingDays(_))
    ));
    assert_eq!(Side::parse("BUY"), Ok(Side::Buy));
    assert_eq!(Side::parse("sell"), Ok(Side::Sell));
    assert!(Side::parse("short").is_err());
    // The notional ignores the side's sign conventions: qty is unsigned.
    assert!((trade(Side::Buy, 10.0, 5.0).notional() - 50.0).abs() < 1e-12);
}
