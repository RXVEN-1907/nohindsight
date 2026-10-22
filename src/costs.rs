//! nohindsight costs - the reality tax, itemized.
//!
//! A backtest without costs is a fantasy with good marketing.  Every trade
//! pays four honest lines:
//!
//! * `fee`          - what your broker charges        ([`FeeSchedule`])
//! * `spread_cost`  - what crossing the spread costs ([`SpreadModel`])
//! * `impact_cost`  - what moving the market costs   ([`ImpactModel`])
//! * `borrow_cost`  - what being short overnight costs ([`BorrowModel`])
//!
//! Impact follows the square-root law - `impact_bps = coeff * sigma_bps *
//! sqrt(qty / adv)` - the empirical regularity documented by Bouchaud,
//! Farmer & Lillo's studies of order flow and formalized in the optimal
//! execution framework of Almgren & Chriss (2000): quadruple the size,
//! double the impact.
//!
//! Costs is the one department that needs no clock: a Trade carries its own
//! holding_days and nothing here reads data, so nothing here can peek at
//! the future.  The dishonesty costs enable is not lookahead - it is the
//! quiet subtraction that turns a real edge into noise.  [`fantasy_vs_real`]
//! exists to make that subtraction loud.
//!
//! Zero dependencies beyond the standard library.

use std::error::Error;
use std::fmt;

use crate::stats::fsum;

/// Which side of the book the trade lands on.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Side {
    /// Buy to open / sell to close a long.
    Buy,
    /// Sell - treated as holding a SHORT position for `holding_days`; borrow
    /// is charged on sells only.
    Sell,
}

impl Side {
    /// The word the CSVs and the Python engine use.
    pub fn parse(word: &str) -> Result<Side, CostsError> {
        match word.trim().to_ascii_lowercase().as_str() {
            "buy" => Ok(Side::Buy),
            "sell" => Ok(Side::Sell),
            other => Err(CostsError::BadSide(other.to_string())),
        }
    }

    /// The canonical word.
    pub fn as_str(self) -> &'static str {
        match self {
            Side::Buy => "buy",
            Side::Sell => "sell",
        }
    }
}

impl fmt::Display for Side {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

/// Trade/costs input failures.  Every message names the field and the value.
#[derive(Clone, Debug, PartialEq)]
pub enum CostsError {
    /// side must be 'buy' or 'sell'.
    BadSide(String),
    /// symbol must be non-empty.
    EmptySymbol,
    /// price must be positive.
    NonPositivePrice(f64),
    /// qty is unsigned size (direction lives in side).
    NegativeQty(f64),
    /// holding_days must be >= 0.
    NegativeHoldingDays(f64),
    /// The impact model kind must be 'sqrt' or 'linear'.
    BadImpactKind(String),
    /// A stats input was refused (fantasy_vs_real).
    Stats(crate::stats::StatsError),
}

impl fmt::Display for CostsError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            CostsError::BadSide(s) => write!(f, "side must be 'buy' or 'sell', got {s:?}"),
            CostsError::EmptySymbol => write!(f, "symbol must be non-empty"),
            CostsError::NonPositivePrice(p) => {
                write!(f, "price must be positive, got {p}")
            }
            CostsError::NegativeQty(q) => {
                write!(f, "qty is unsigned size (direction lives in side), got {q}")
            }
            CostsError::NegativeHoldingDays(d) => {
                write!(f, "holding_days must be >= 0, got {d}")
            }
            CostsError::BadImpactKind(k) => {
                write!(f, "kind must be 'sqrt' or 'linear', got {k:?}")
            }
            CostsError::Stats(e) => write!(f, "{e}"),
        }
    }
}

impl Error for CostsError {}

impl From<crate::stats::StatsError> for CostsError {
    fn from(e: crate::stats::StatsError) -> Self {
        CostsError::Stats(e)
    }
}

/// One executed (or hypothetical) trade, as the costs module sees it.
///
/// Conventions, stated so they can be argued with:
///
/// * `qty` is UNSIGNED size; direction lives in `side`.  A negative qty is
///   a bug, and the constructor says so.
/// * `side == Sell` is treated as holding a SHORT position for
///   `holding_days` - borrow is charged on sells only.  This model prices
///   one-shot trades, not position timelines; a long round trip is
///   buy-then-sell and pays borrow on neither leg.
/// * `ts` is stored exactly as given (an ISO string); this module never
///   parses it.  Persistence layers should pass ISO strings.
/// * `sigma_bps` (daily volatility in bps) and `adv` (average daily volume,
///   same units as qty) are optional context.  Without them the impact line
///   comes back zero with `impact_explained = false` - see
///   [`ImpactModel::estimate`] for why that distinction matters.
#[derive(Clone, Debug, PartialEq)]
pub struct Trade {
    pub ts: String,
    pub symbol: String,
    pub side: Side,
    pub price: f64,
    pub qty: f64,
    pub sigma_bps: Option<f64>,
    pub adv: Option<f64>,
    pub holding_days: f64,
}

impl Trade {
    /// Validate and build (see the field docs for the conventions).
    // Eight arguments on purpose: this constructor mirrors the Python
    // dataclass field-for-field, so the two engines' trade records line up
    // in review.  Callers with optional context should use `..Default`-style
    // helpers of their own.
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        ts: &str,
        symbol: &str,
        side: Side,
        price: f64,
        qty: f64,
        sigma_bps: Option<f64>,
        adv: Option<f64>,
        holding_days: f64,
    ) -> Result<Trade, CostsError> {
        if symbol.is_empty() {
            return Err(CostsError::EmptySymbol);
        }
        if price <= 0.0 {
            return Err(CostsError::NonPositivePrice(price));
        }
        if qty < 0.0 {
            return Err(CostsError::NegativeQty(qty));
        }
        if holding_days < 0.0 {
            return Err(CostsError::NegativeHoldingDays(holding_days));
        }
        Ok(Trade {
            ts: ts.to_string(),
            symbol: symbol.to_string(),
            side,
            price,
            qty,
            sigma_bps,
            adv,
            holding_days,
        })
    }

    /// price * qty - the money the trade moves, independent of side.
    pub fn notional(&self) -> f64 {
        self.price.abs() * self.qty.abs()
    }
}

/// The itemized reality tax on one trade, in monetary units.
///
/// `fee`, `spread_cost`, `impact_cost` and `borrow_cost` are the four stored
/// lines.  [`CostBreakdown::total`] and [`CostBreakdown::total_bps`] are
/// DERIVED: a breakdown cannot disagree with its own parts.  `notional` is
/// stored because total_bps is a rate and a breakdown without its base
/// cannot state one.
///
/// `impact_explained` is false when an impact model ran but lacked
/// sigma/ADV.  Reports should then say "impact unestimated", not "0" - a
/// silent zero is how impact quietly vanishes from a backtest.
#[derive(Clone, Copy, Debug, PartialEq, Default)]
pub struct CostBreakdown {
    pub fee: f64,
    pub spread_cost: f64,
    pub impact_cost: f64,
    pub borrow_cost: f64,
    pub notional: f64,
    pub impact_explained: bool,
}

impl CostBreakdown {
    /// fee + spread + impact + borrow, in monetary units.
    pub fn total(&self) -> f64 {
        self.fee + self.spread_cost + self.impact_cost + self.borrow_cost
    }

    /// total as basis points of notional (0.0 when notional is 0).
    pub fn total_bps(&self) -> f64 {
        if self.notional <= 0.0 {
            return 0.0;
        }
        self.total() / self.notional * 1e4
    }
}

/// Commission model.  Both lines add, so a broker charging bps AND
/// per-share is expressible; [`FeeSchedule::zero`] is the common fantasy.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct FeeSchedule {
    pub bps: f64,
    pub cents_per_share: f64,
}

impl FeeSchedule {
    /// Basis points of notional per trade.  `flat_bps(10)` on $1M -> $1000.
    pub fn flat_bps(bps: f64) -> FeeSchedule {
        FeeSchedule {
            bps,
            cents_per_share: 0.0,
        }
    }

    /// Cents per share.  `per_share(0.5)` on 1000 shares -> $5.00.
    pub fn per_share(cents: f64) -> FeeSchedule {
        FeeSchedule {
            bps: 0.0,
            cents_per_share: cents,
        }
    }

    /// The free lunch.  It is a lie, but a configurable one.
    pub fn zero() -> FeeSchedule {
        FeeSchedule {
            bps: 0.0,
            cents_per_share: 0.0,
        }
    }

    /// Total commission, in the same monetary units as `notional`.
    pub fn apply(&self, notional: f64, shares: f64) -> f64 {
        notional * self.bps / 1e4 + shares * self.cents_per_share / 100.0
    }
}

/// The cost of crossing the spread.
///
/// THE CONVENTION - READ THIS TWICE:
///
/// A quote is bid/ask around a mid.  A market order does not trade at the
/// mid; it CROSSES, paying the HALF-spread (ask-mid for a buy, mid-bid for
/// a sell).  In this module one Trade record is one crossing and pays
/// exactly one half-spread:
///
/// `cost = price * qty * (spread_bps / 2) / 1e4`
///
/// A round trip is TWO Trade records (entry and exit) and pays two
/// half-spreads - i.e. exactly the full spread, once.  If your numbers show
/// half a spread per round trip here, you have built a strategy that trades
/// in a market with no other side.  Conversely: do not "correct" this to a
/// full spread per trade, or you will pay the spread twice.
///
/// Volatility scaling: real spreads widen when volatility spikes.
/// [`SpreadModel::volatility_scaled`] multiplies the base by
/// (1 + mult * sigma_bps/1000) when the trade carries a sigma, and leaves
/// the base untouched when it does not - an unestimated sigma is not a calm
/// market, but we cannot charge for fear we cannot see.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct SpreadModel {
    pub base_bps: f64,
    pub mult: Option<f64>,
}

impl SpreadModel {
    /// A spread that never moves.  Convenient; never true.
    pub fn constant(bps: f64) -> SpreadModel {
        SpreadModel {
            base_bps: bps,
            mult: None,
        }
    }

    /// spread_bps = base_bps * (1 + mult * sigma_bps/1000) when sigma known.
    pub fn volatility_scaled(base_bps: f64, mult: f64) -> SpreadModel {
        SpreadModel {
            base_bps,
            mult: Some(mult),
        }
    }

    /// The full bid-ask spread in bps under this model.
    pub fn spread_bps(&self, sigma_bps: Option<f64>) -> f64 {
        match self.mult {
            None => self.base_bps,
            Some(mult) => match sigma_bps {
                None => self.base_bps,
                Some(sigma) => self.base_bps * (1.0 + mult * sigma / 1000.0),
            },
        }
    }

    /// Cost in monetary units of ONE crossing at this spread.
    ///
    /// Worked example: constant(20), price=100, qty=10 -> half-spread is
    /// 10 bps = 0.001 of the $1,000 notional -> cost = 100 * 10 * 0.001 =
    /// 1.00.  The optional sigma_bps exists because volatility-scaled
    /// spreads need the trade's volatility; a call without it means
    /// "sigma unknown".
    pub fn half_cost(&self, price: f64, qty: f64, sigma_bps: Option<f64>) -> f64 {
        let half_bps = self.spread_bps(sigma_bps) / 2.0;
        price * qty * half_bps / 1e4
    }
}

/// Which impact law to apply.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ImpactKind {
    /// impact_bps = coeff * sigma_bps * sqrt(qty / adv) - the square-root
    /// law (Bouchaud, Farmer & Lillo; Almgren & Chriss 2000).
    Sqrt,
    /// impact_bps = eta * (qty / adv) - the linear alternative, kept for
    /// comparison: it is what naive execution models assume and what the
    /// square-root law says is far too kind at size.
    Linear,
}

/// Price impact - you are not the only one in the book.
///
/// Impact grows with the square root of participation (qty/adv), is
/// measured in bps of the trade's notional, and scales with the day's
/// volatility - a violent market moves further when you push it.
/// coeff=1.0 is a placeholder, not a law of nature; calibrate it against
/// your own fills and stop trusting the default.
///
/// If a required input is missing (ADV always; sigma for the sqrt law) the
/// model returns impact 0.0 with explained=false: an unestimated impact is
/// NOT a zero impact, and the flag exists so reports can say "impact
/// unestimated - no sigma/ADV" out loud instead of silently booking a
/// costless whale trade.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ImpactModel {
    pub kind: ImpactKind,
    pub coeff: f64,
    pub eta: f64,
}

impl ImpactModel {
    /// The square-root law with a calibration coefficient (default 1.0).
    pub fn sqrt_law(coeff: f64) -> ImpactModel {
        ImpactModel {
            kind: ImpactKind::Sqrt,
            coeff,
            eta: 0.1,
        }
    }

    /// The linear model with slope eta (default 0.1).  Sigma-independent.
    pub fn linear(eta: f64) -> ImpactModel {
        ImpactModel {
            kind: ImpactKind::Linear,
            coeff: 1.0,
            eta,
        }
    }

    /// Return (impact in bps of notional, explained).
    ///
    /// explained is false exactly when a required input was missing; impact
    /// 0.0 then means "could not estimate", never "no impact".  With all
    /// inputs present the flag is true even when the formula legitimately
    /// evaluates to 0 (qty == 0 is a real zero, not a missing estimate).
    pub fn estimate(&self, qty: f64, adv: Option<f64>, sigma_bps: Option<f64>) -> (f64, bool) {
        let adv = match adv {
            Some(a) if a > 0.0 => a,
            _ => return (0.0, false),
        };
        let participation = qty / adv;
        match self.kind {
            ImpactKind::Sqrt => match sigma_bps {
                None => (0.0, false),
                Some(sigma) => (self.coeff * sigma * participation.sqrt(), true),
            },
            ImpactKind::Linear => (self.eta * participation, true),
        }
    }

    /// Convenience form of [`ImpactModel::estimate`] that discards the flag.
    pub fn impact_bps(&self, qty: f64, adv: Option<f64>, sigma_bps: Option<f64>) -> f64 {
        self.estimate(qty, adv, sigma_bps).0
    }
}

/// Stock borrow: shorts pay rent.
///
/// `cost = notional * annual_rate * holding_days / 365`
///
/// Simplifications, stated: one flat rate for every name (no
/// hard-to-borrow tiers), calendar-day accrual over a 365-day year, no
/// business-day nuance.  A sell is a short (see [`Trade`]); buys pay
/// nothing.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct BorrowModel {
    pub rate: f64,
}

impl BorrowModel {
    /// A flat annual rate, prorated by holding period.
    pub fn annual(rate: f64) -> BorrowModel {
        BorrowModel { rate }
    }

    /// Borrow charge for holding `notional` short for `holding_days`.
    pub fn cost(&self, notional: f64, holding_days: f64) -> f64 {
        notional * self.rate * holding_days / 365.0
    }
}

/// The composed reality tax: fees + spread + impact + borrow.
///
/// Every component left `None` is a cost line of zero.  The model will not
/// remind you that you forgot impact - the impact_explained flag only fires
/// when an impact model EXISTS but lacked sigma/ADV.  If you want the report
/// to be able to warn, pass an impact model.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct CostModel {
    pub fees: FeeSchedule,
    pub spread: SpreadModel,
    pub impact: Option<ImpactModel>,
    pub borrow: Option<BorrowModel>,
}

impl Default for CostModel {
    fn default() -> CostModel {
        CostModel {
            fees: FeeSchedule::zero(),
            spread: SpreadModel::constant(0.0),
            impact: None,
            borrow: None,
        }
    }
}

impl CostModel {
    /// Itemize the four cost lines for one trade.
    pub fn price(&self, trade: &Trade) -> CostBreakdown {
        let qty = trade.qty.abs();
        let notional = trade.notional();
        let fee = self.fees.apply(notional, qty);
        let spread_cost = self.spread.half_cost(trade.price, qty, trade.sigma_bps);
        let (impact_cost, explained) = match self.impact {
            None => (0.0, true),
            Some(impact) => {
                let (bps, explained) = impact.estimate(qty, trade.adv, trade.sigma_bps);
                (notional * bps / 1e4, explained)
            }
        };
        let borrow_cost = match self.borrow {
            Some(borrow) if trade.side == Side::Sell => borrow.cost(notional, trade.holding_days),
            _ => 0.0,
        };
        CostBreakdown {
            fee,
            spread_cost,
            impact_cost,
            borrow_cost,
            notional,
            impact_explained: explained,
        }
    }

    /// Price every trade, in order.
    pub fn apply(&self, trades: &[Trade]) -> Vec<CostBreakdown> {
        trades.iter().map(|t| self.price(t)).collect()
    }

    /// Aggregate cost rate: total cost over total notional, in bps.
    ///
    /// Weighted by notional, not averaged - 1bp on $1M plus 100bps on $10
    /// is 1.001bps, not 50.5.  Zero notional trades cost nothing and say so
    /// (0.0, not a division by zero).
    pub fn total_bps(&self, trades: &[Trade]) -> f64 {
        let breakdowns = self.apply(trades);
        let total = fsum(breakdowns.iter().map(|b| b.total()));
        let notional = fsum(breakdowns.iter().map(|b| b.notional));
        if notional <= 0.0 {
            return 0.0;
        }
        total / notional * 1e4
    }
}

/// Sharpe before costs, Sharpe after costs, and the shrinkage between.
///
/// This is the function that turns "0.1% per trade is nothing" into a
/// number.
///
/// Simplification, stated: the per-period drag costs_bps/1e4 is subtracted
/// from the MEAN per-period return while the standard deviation is left
/// untouched - costs are modeled as a deterministic levy uncorrelated with
/// returns.  Compounding and the dispersion of per-trade costs are
/// second-order here; the first-order truth this function exists for is
/// that a small per-trade cost applied at high turnover is not small.
///
/// Sharpe convention: sample standard deviation (ddof=1), annualized by
/// sqrt(periods_per_year) - the same convention forensics uses.
///
/// `shrinkage` is NaN when the pre-cost Sharpe is exactly zero (the ratio
/// is undefined; the absolute after-cost Sharpe tells the story).  Above
/// 1.0 for a losing strategy: costs hurt bad strategies too, and the ratio
/// says so honestly.  Zero-variance returns have an undefined Sharpe; we
/// return 0.0 rather than pretend a constant series is an edge.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct FantasyVsReal {
    pub sharpe_before: f64,
    pub sharpe_after: f64,
    pub shrinkage: f64,
    pub drag_per_period: f64,
    pub n_periods: usize,
}

/// See [`FantasyVsReal`] for the returned fields and their conventions.
pub fn fantasy_vs_real(
    returns_no_cost: &[f64],
    costs_bps: f64,
    periods_per_year: u32,
) -> Result<FantasyVsReal, CostsError> {
    let n = returns_no_cost.len();
    if n < 2 {
        return Err(CostsError::Stats(
            crate::stats::StatsError::NotEnoughObservations {
                what: "fantasy_vs_real".to_string(),
                need: 2,
                got: n,
            },
        ));
    }
    let mean = fsum(returns_no_cost.iter().copied()) / n as f64;
    let ss = fsum(returns_no_cost.iter().map(|x| (x - mean) * (x - mean)));
    let std = (ss / (n - 1) as f64).sqrt();
    let drag = costs_bps / 1e4;
    let ann = (periods_per_year as f64).sqrt();
    let (before, after) = if std > 0.0 {
        ((mean / std) * ann, ((mean - drag) / std) * ann)
    } else {
        (0.0, 0.0)
    };
    let ratio = if before != 0.0 {
        after / before
    } else {
        f64::NAN
    };
    Ok(FantasyVsReal {
        sharpe_before: before,
        sharpe_after: after,
        shrinkage: ratio,
        drag_per_period: drag,
        n_periods: n,
    })
}

// ---------------------------------------------------------------------------
// tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fee_schedules_add_both_lines() {
        assert!((FeeSchedule::flat_bps(10.0).apply(1_000_000.0, 0.0) - 1000.0).abs() < 1e-9);
        assert!((FeeSchedule::per_share(0.5).apply(0.0, 1000.0) - 5.0).abs() < 1e-12);
        let both = FeeSchedule {
            bps: 10.0,
            cents_per_share: 0.5,
        };
        assert!((both.apply(1_000_000.0, 1000.0) - 1005.0).abs() < 1e-9);
        assert_eq!(FeeSchedule::zero().apply(1e9, 1e6), 0.0);
    }

    #[test]
    fn half_spread_convention_matches_the_worked_example() {
        // constant(20), price=100, qty=10 -> cost 1.00 for ONE crossing.
        let spread = SpreadModel::constant(20.0);
        assert!((spread.half_cost(100.0, 10.0, None) - 1.0).abs() < 1e-12);
        // A round trip is two records: exactly the full spread, once.
        let round_trip = spread.half_cost(100.0, 10.0, None) * 2.0;
        assert!((round_trip - 100.0 * 10.0 * 20.0 / 1e4).abs() < 1e-12);
        // Volatility scaling widens only when sigma is present.
        let scaled = SpreadModel::volatility_scaled(20.0, 8.0);
        assert_eq!(scaled.spread_bps(None), 20.0);
        assert!(
            (scaled.spread_bps(Some(100.0)) - 20.0 * (1.0 + 8.0 * 100.0 / 1000.0)).abs() < 1e-12
        );
    }

    #[test]
    fn sqrt_impact_scales_with_the_square_root() {
        let impact = ImpactModel::sqrt_law(1.0);
        let base = impact.estimate(100.0, Some(10_000.0), Some(200.0));
        assert_eq!(base, (200.0f64 * (100.0f64 / 10_000.0).sqrt(), true));
        // Quadruple the size, double the impact.
        let big = impact.estimate(400.0, Some(10_000.0), Some(200.0));
        assert!((big.0 - 2.0 * base.0).abs() < 1e-12);
        // Missing ADV or sigma: zero, but NOT explained - that is the flag's
        // whole reason to exist.
        assert_eq!(impact.estimate(100.0, None, Some(200.0)), (0.0, false));
        assert_eq!(impact.estimate(100.0, Some(10_000.0), None), (0.0, false));
        // qty == 0 with full inputs is a REAL zero, not a missing estimate.
        assert_eq!(
            impact.estimate(0.0, Some(10_000.0), Some(200.0)),
            (0.0, true)
        );
        // Linear is participation-linear and sigma-free.
        let linear = ImpactModel::linear(0.1);
        let (bps, explained) = linear.estimate(250.0, Some(10_000.0), None);
        assert!((bps - 0.0025).abs() < 1e-15);
        assert!(explained);
    }

    #[test]
    fn borrow_prorates_calendar_days() {
        let borrow = BorrowModel::annual(0.05);
        assert!((borrow.cost(100_000.0, 36.5) - 500.0).abs() < 1e-9);
        assert_eq!(borrow.cost(1e9, 0.0), 0.0);
    }

    #[test]
    fn composition_sums_and_weights_by_notional() {
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
        let impact_bps = 200.0f64 * (10.0f64 / 10_000.0).sqrt(); // ~6.32 bps
        let expected = 1000.0 * 10.0 / 1e4   // fee: 10bp of the $1,000 notional
            + 1000.0 * 10.0 / 1e4            // HALF of a 20bp spread = 10bp
            + 1000.0 * impact_bps / 1e4      // impact at ~6.32bp
            + 1000.0 * 0.05 * 36.5 / 365.0; // 5% for 36.5 days
        assert!((b.total() - expected).abs() < 1e-9);
        assert!(b.impact_explained);
        // Notional-weighted aggregate, not an average of rates.
        let small = Trade::new("t2", "TEST", Side::Buy, 1.0, 10.0, None, None, 0.0).expect("ok");
        let small_total = model.price(&small).total();
        let small_notional = small.notional();
        let agg = model.total_bps(&[t, small]);
        let total = b.total() + small_total;
        let notional = b.notional + small_notional;
        assert!((agg - total / notional * 1e4).abs() < 1e-9);
    }

    #[test]
    fn fantasy_vs_real_shrinks_monotonically() {
        let returns: Vec<f64> = (0..250)
            .map(|i| if i % 2 == 0 { 0.004 } else { -0.002 })
            .collect();
        let free = fantasy_vs_real(&returns, 0.0, 252).expect("ok");
        assert!((free.shrinkage - 1.0).abs() < 1e-12);
        assert!((free.drag_per_period - 0.0).abs() < 1e-15);
        let cheap = fantasy_vs_real(&returns, 1.0, 252).expect("ok");
        let dear = fantasy_vs_real(&returns, 10.0, 252).expect("ok");
        assert!(free.sharpe_after > cheap.sharpe_after);
        assert!(
            cheap.sharpe_after > dear.sharpe_after,
            "more cost, less sharpe"
        );
        assert!((dear.drag_per_period - 10.0 / 1e4).abs() < 1e-15);
        assert!(dear.shrinkage < cheap.shrinkage && cheap.shrinkage < 1.0);
        assert!(fantasy_vs_real(&[0.01], 1.0, 252).is_err());
        // A losing strategy: costs shrink it further, shrinkage > 1.
        let losing: Vec<f64> = (0..250)
            .map(|i| if i % 2 == 0 { -0.004 } else { 0.002 })
            .collect();
        let hurt = fantasy_vs_real(&losing, 5.0, 252).expect("ok");
        assert!(hurt.shrinkage > 1.0);
    }

    #[test]
    fn trade_validation_refuses_nonsense() {
        assert!(Trade::new("t", "", Side::Buy, 10.0, 1.0, None, None, 0.0).is_err());
        assert!(Trade::new("t", "S", Side::Buy, 0.0, 1.0, None, None, 0.0).is_err());
        assert!(Trade::new("t", "S", Side::Buy, 10.0, -1.0, None, None, 0.0).is_err());
        assert!(Trade::new("t", "S", Side::Buy, 10.0, 1.0, None, None, -1.0).is_err());
        assert_eq!(Side::parse("BUY"), Ok(Side::Buy));
        assert_eq!(
            Side::parse("short"),
            Err(CostsError::BadSide("short".to_string()))
        );
    }
}
