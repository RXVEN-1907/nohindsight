//! nohindsight demo — the whole story in one run.
//!
//! One synthetic market, five planted crimes, two strategies that differ in
//! exactly one closure, and the two departments that judge them:
//!
//! 1. Build synthetic prices (seed [`PRICE_SEED`]) and fundamentals (seed
//!    [`FUND_SEED`]).  Four data crimes are planted in the prices; one
//!    restatement is planted in the fundamentals: SYM2 filed for its last
//!    quarter, then re-filed 30% lower three weeks later.
//! 2. The data police ([`crate::checks`]) read SYM3's bars and catch the
//!    raw 4:1 split.
//! 3. Two strategies run the SAME trading logic through the SAME replay
//!    engine, with the same fees and the same starting cash.  The only
//!    difference is how they read SYM2's revenue:
//!
//!    * honest — through an [`AsOfView`][crate::core::AsOfView] bound to the
//!      replay clock: only what the world had published by then;
//!    * naive — `Ledger::latest(key)`: today's terminal number merged onto
//!      every historical decision date.  This is what every beginner's
//!      pandas merge does silently.
//!
//!    The gate (identical for both): hold SYM2 while quarterly revenue is
//!    at least [`FLOOR`] ($200m), park in SYM1 otherwise.  The floor is a
//!    plain a-priori quality bar; it happens to sit between what SYM2 filed
//!    and what it later admitted, which is exactly the seam the restatement
//!    opens.
//! 4. [`crate::forensics::audit`] interrogates both return streams
//!    (n_trials = 1).
//! 5. The leak receipt: how many decision days the naive read ran on
//!    knowledge the world did not have yet, and the worst lag.
//! 6. The closing line, then the honest verdict in full.
//!
//! Deterministic end to end: same seeds, same words, every run.  (The
//! Python demo also writes an HTML report; the engine's story lives on
//! stdout — the terminal is the artifact here.)
//!
//! THE SEEDS, stated like any good exhibit: [`FUND_SEED`] is picked so the
//! restatement straddles the floor (the original above it, the re-filed
//! number below it, and the previous quarter's number also above it so the
//! honest strategy is already in the name), and [`PRICE_SEED`] is picked so
//! the inversion is visible: the honest strategy rides SYM2 through the
//! leak window's weather while the naive one was already parked in the
//! winner.  Neither was the first seed tried — [`find_story_seed`] is the
//! scan that found them, kept in the library so the choice can be replayed
//! and audited instead of being folklore.

use crate::checks::{self, Severity};
use crate::core::{AsOfView, Clock, Ledger, Time, Value, NANOS_PER_SEC};
use crate::forensics::{audit, AuditConfig, Level, Verdict};
use crate::synth::{bar_history, synthetic_fundamentals, synthetic_prices, trading_day_stamps};
use std::error::Error;
use std::fmt;
use std::io::IsTerminal;

// -- the demo's own constants (stated, not hidden) -------------------------

pub const GATED: &str = "SYM2"; // the symbol whose revenue carries the restatement
pub const PARK: &str = "SYM1"; // where the strategy sits when the gate is closed
pub const GATED_REVENUE: &str = "SYM2.revenue";
pub const FLOOR: f64 = 200.0; // $200m quarterly revenue: the a-priori quality bar
pub const WEIGHT: f64 = 0.35; // target position, as a fraction of starting cash
pub const STARTING_CASH: f64 = 100_000.0;
pub const FEE_BPS: f64 = 1.0; // flat commission, both engines, both sides
pub const N_DAYS: usize = 750;

/// The price-seed exhibit: under this seed the naive strategy posts the
/// higher Sharpe (1.14 vs 0.69) AND lands in a different verdict level than
/// the honest one — REAL against SUSPICIOUS — the inversion the whole
/// library exists to explain.  Found by [`find_story_seed`] (first pair it
/// returns, scanning fund seeds from 1 and price seeds from 1); the scan is
/// kept in the library so the choice is re-derivable, not folklore.
pub const PRICE_SEED: u64 = 22;
/// The fundamentals-seed exhibit: the original filing clears the floor,
/// the restated number does not, and the previous quarter (the number the
/// honest world starts on) clears the floor too.
pub const FUND_SEED: u64 = 3;

/// Demo-level failures.
#[derive(Clone, Debug, PartialEq)]
pub enum DemoError {
    /// The synth generators refused something.
    Ledger(crate::core::LedgerError),
    /// The audit refused a stream.
    Forensics(crate::forensics::ForensicsError),
    /// The world is missing a crime the story depends on (a seed broke the
    /// narrative contract — see the constants above).
    MissingCrime(&'static str),
    /// The replay clock refused to advance (only possible if the stamps are
    /// not monotonic, which the generator guarantees).
    Clock(crate::core::ClockError),
}

impl fmt::Display for DemoError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            DemoError::Ledger(e) => write!(f, "synthetic world: {e}"),
            DemoError::Forensics(e) => write!(f, "forensics: {e}"),
            DemoError::MissingCrime(what) => {
                write!(f, "the story needs {what}; the pinned seeds broke it")
            }
            DemoError::Clock(e) => write!(f, "replay clock: {e}"),
        }
    }
}

impl Error for DemoError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            DemoError::Ledger(e) => Some(e),
            DemoError::Forensics(e) => Some(e),
            _ => None,
        }
    }
}

impl From<crate::core::LedgerError> for DemoError {
    fn from(e: crate::core::LedgerError) -> Self {
        DemoError::Ledger(e)
    }
}

impl From<crate::forensics::ForensicsError> for DemoError {
    fn from(e: crate::forensics::ForensicsError) -> Self {
        DemoError::Forensics(e)
    }
}

impl From<crate::core::ClockError> for DemoError {
    fn from(e: crate::core::ClockError) -> Self {
        DemoError::Clock(e)
    }
}

// -- the world -------------------------------------------------------------

/// One built synthetic world: the two ledgers plus the aligned day-grid the
/// replay walks.
pub struct World {
    pub prices: Ledger,
    pub funds: Ledger,
    pub stamps: Vec<Time>,
    /// PARK's close per trading day (SYM1 never gaps).
    pub park: Vec<crate::core::Bar>,
    /// GATED's close per trading day (SYM2 never gaps).
    pub gated: Vec<crate::core::Bar>,
}

/// Align one symbol's bar history onto the stamp grid (both sorted; a
/// linear merge).  A missing day would be a gap, which SYM1/SYM2 never
/// have — a short vector here is a generator bug the caller reports.
fn align(bars: &[(Time, crate::core::Bar)], stamps: &[Time]) -> Vec<crate::core::Bar> {
    let mut out = Vec::with_capacity(stamps.len());
    let mut cursor = 0usize;
    for stamp in stamps {
        while cursor < bars.len() && bars[cursor].0 < *stamp {
            cursor += 1;
        }
        if cursor < bars.len() && bars[cursor].0 == *stamp {
            out.push(bars[cursor].1);
        }
    }
    out
}

/// Build the synthetic world for one (price, fund) seed pair.
pub fn build_world(price_seed: u64, fund_seed: u64) -> Result<World, DemoError> {
    let prices = synthetic_prices(N_DAYS, price_seed)?;
    let funds = synthetic_fundamentals(8, 8, fund_seed)?;
    let stamps = trading_day_stamps(
        Time::parse("2024-01-02").expect("the demo's fixed calendar anchor"),
        N_DAYS,
    );
    let park = align(&bar_history(&prices, PARK), &stamps);
    let gated = align(&bar_history(&prices, GATED), &stamps);
    if park.len() != stamps.len() || gated.len() != stamps.len() {
        return Err(DemoError::MissingCrime("a full price grid for SYM1/SYM2"));
    }
    Ok(World {
        prices,
        funds,
        stamps,
        park,
        gated,
    })
}

/// (original, restated) entries for the planted SYM2 restatement.
pub fn restatement(
    funds: &Ledger,
) -> Result<(&crate::core::Entry, &crate::core::Entry), DemoError> {
    let history = funds.history(GATED_REVENUE);
    let restated = history
        .last()
        .ok_or(DemoError::MissingCrime("a SYM2.revenue history"))?;
    let original = history
        .iter()
        .rev()
        .skip(1)
        .find(|e| e.knowledge_time < restated.knowledge_time)
        .ok_or(DemoError::MissingCrime(
            "an original filing before the restatement",
        ))?;
    Ok((original, restated))
}

// -- the two strategies ----------------------------------------------------

/// Shared trading logic.  The rule, identical for both: while the known
/// quarterly revenue is at least [`FLOOR`], hold GATED; otherwise hold
/// PARK.  Sizing is a fixed [`WEIGHT`] of starting cash at each entry
/// price (the demo's stated simplification: it does not track account
/// drift between its two switches).  Decisions use the bar that just
/// closed; fills happen at that same close, minus the flat fee each way.
///
/// `read_revenue` is the single closure the two strategies disagree on.
pub fn replay(
    mut read_revenue: impl FnMut() -> Option<f64>,
    park_bars: &[crate::core::Bar],
    gated_bars: &[crate::core::Bar],
    stamps: &[Time],
    clock: &Clock,
) -> Result<EngineResult, DemoError> {
    let fee = FEE_BPS / 1e4;
    let mut cash = STARTING_CASH;
    let mut shares = 0.0f64;
    let mut holding: Option<usize> = None; // 0 = PARK, 1 = GATED
    let mut hold_days = 0usize;
    let mut n_switches = 0usize;
    let mut equity_curve: Vec<(Time, f64)> = Vec::with_capacity(stamps.len());

    for (i, ts) in stamps.iter().enumerate() {
        clock.advance_to(*ts)?;
        let revenue = read_revenue();
        let gate_open = matches!(revenue, Some(r) if r >= FLOOR);
        let target = if gate_open { 1usize } else { 0usize };
        if Some(target) != holding {
            // Sell whatever we hold, at its close, paying the fee.
            if let Some(h) = holding {
                let close = if h == 1 {
                    gated_bars[i].close
                } else {
                    park_bars[i].close
                };
                cash += shares * close * (1.0 - fee);
                shares = 0.0;
            }
            // Buy the target at its close, paying the fee.
            let close = if target == 1 {
                gated_bars[i].close
            } else {
                park_bars[i].close
            };
            let qty = (WEIGHT * STARTING_CASH / close).floor();
            if qty >= 1.0 {
                cash -= qty * close * (1.0 + fee);
                shares = qty;
                holding = Some(target);
                n_switches += 1;
            }
        }
        if holding == Some(1) {
            hold_days += 1;
        }
        let equity = match holding {
            Some(1) => cash + shares * gated_bars[i].close,
            Some(_) => cash + shares * park_bars[i].close,
            None => cash,
        };
        equity_curve.push((*ts, equity));
    }

    Ok(EngineResult {
        equity_curve,
        hold_days,
        n_switches,
    })
}

/// What one replay run produced.
#[derive(Clone, Debug, PartialEq)]
pub struct EngineResult {
    pub equity_curve: Vec<(Time, f64)>,
    /// Days spent in the gated symbol.
    pub hold_days: usize,
    pub n_switches: usize,
}

/// Daily returns from an equity curve (curve[i]/curve[i-1] - 1).
pub fn returns_of(curve: &[(Time, f64)]) -> Vec<f64> {
    curve.windows(2).map(|w| w[1].1 / w[0].1 - 1.0).collect()
}

// -- the leak accounting ---------------------------------------------------

/// The naive read's crime, itemized.  The naive read returns the terminal
/// revision on every decision day; for every day before that revision's
/// knowledge_time, the read used knowledge the world did not have yet.
/// This is the post-hoc version of what a runtime guard would have raised
/// on the spot.
#[derive(Clone, Debug, PartialEq)]
pub struct LeakReceipt {
    pub key: String,
    pub decision_days_leaked: usize,
    pub first_leak_day: Time,
    pub knowledge_time: Time,
    pub max_days_early: i64,
}

/// Itemize the naive read over the demo's decision-day grid.
pub fn leak_receipt(funds: &Ledger, stamps: &[Time]) -> Result<LeakReceipt, DemoError> {
    let restated = funds
        .latest(GATED_REVENUE)
        .ok_or(DemoError::MissingCrime("a terminal SYM2.revenue revision"))?;
    let leak_days: Vec<Time> = stamps
        .iter()
        .filter(|ts| **ts < restated.knowledge_time)
        .copied()
        .collect();
    let first = *leak_days.first().ok_or(DemoError::MissingCrime(
        "a decision day before the restatement",
    ))?;
    Ok(LeakReceipt {
        key: GATED_REVENUE.to_string(),
        decision_days_leaked: leak_days.len(),
        first_leak_day: first,
        knowledge_time: restated.knowledge_time,
        // timedelta.days floors; so does integer division here.
        max_days_early: restated.knowledge_time.diff_nanos(first) / (86_400 * NANOS_PER_SEC),
    })
}

// -- the seed provenance ---------------------------------------------------

/// What a seed pair must produce for the story to hold.
pub struct StorySeed {
    pub price_seed: u64,
    pub fund_seed: u64,
    pub honest_sharpe: f64,
    pub naive_sharpe: f64,
}

/// The fund half of the contract: the number knowable on day one clears
/// the floor, the original filing clears it, and the restated number does
/// not — the seam the demo's gate falls into.
fn fund_seed_opens_the_seam(funds: &Ledger, first_stamp: Time) -> bool {
    let history = funds.history(GATED_REVENUE);
    if history.len() < 9 {
        return false;
    }
    let restated = match history.last() {
        Some(e) => e,
        None => return false,
    };
    let restated_value = match restated.value.as_num() {
        Some(v) => v,
        None => return false,
    };
    if restated_value >= FLOOR {
        return false; // the gate would never close: no story
    }
    let original = match history
        .iter()
        .rev()
        .skip(1)
        .find(|e| e.knowledge_time < restated.knowledge_time)
    {
        Some(e) => e,
        None => return false,
    };
    let original_value = match original.value.as_num() {
        Some(v) => v,
        None => return false,
    };
    if original_value < FLOOR {
        return false; // the honest gate would never open
    }
    // The value knowable on day one (the previous quarter's filing).
    funds
        .value_at(GATED_REVENUE, first_stamp)
        .ok()
        .and_then(Value::as_num)
        .is_some_and(|v| v >= FLOOR)
}

/// Run both strategies through the shared engine and audit both streams.
/// Public so the seed provenance scan (and the tests) can see the raw
/// verdicts without the prose.
pub fn run_pair(
    world: &World,
) -> Result<(Verdict, Verdict, EngineResult, EngineResult), DemoError> {
    // The honest strategy's view and the replay engine share ONE clock: it
    // advances, the view reads it.  (A view bound to a DIFFERENT clock is a
    // leak of the opposite kind: it would never learn anything new.)
    let clock = Clock::at(world.stamps[0]);
    let view: AsOfView<'_> = world.funds.view(&clock);
    let honest = replay(
        || view.try_get(GATED_REVENUE).and_then(Value::as_num),
        &world.park,
        &world.gated,
        &world.stamps,
        &clock,
    )?;

    // The naive strategy holds the raw ledger, on purpose, with a comment.
    let naive_clock = Clock::at(world.stamps[0]);
    let naive = replay(
        || {
            world
                .funds
                .latest(GATED_REVENUE)
                .and_then(|e| e.value.as_num())
        },
        &world.park,
        &world.gated,
        &world.stamps,
        &naive_clock,
    )?;

    let cfg = AuditConfig::default();
    let honest_returns = returns_of(&honest.equity_curve);
    let naive_returns = returns_of(&naive.equity_curve);
    let verdict_h = audit(&honest_returns, cfg, None)?;
    let verdict_n = audit(&naive_returns, cfg, None)?;
    Ok((verdict_h, verdict_n, honest, naive))
}

/// Scan seeds for a pair that tells the story, starting at the given
/// numbers.  Criteria, in order: a fund seed whose restatement straddles
/// the floor (see [`fund_seed_opens_the_seam`]), then a price seed whose
/// two verdicts differ — honest SUSPICIOUS and naive REAL — with the naive
/// Sharpe strictly higher.  Deterministic; used to pick the pinned
/// constants, kept public so the choice is auditable.
pub fn find_story_seed(
    from_fund: u64,
    from_price: u64,
    fund_limit: u64,
    price_limit: u64,
) -> Option<StorySeed> {
    for fund_seed in from_fund..from_fund + fund_limit {
        let funds = synthetic_fundamentals(8, 8, fund_seed).ok()?;
        let stamps = trading_day_stamps(
            Time::parse("2024-01-02").expect("the demo's fixed calendar anchor"),
            N_DAYS,
        );
        if !fund_seed_opens_the_seam(&funds, stamps[0]) {
            continue;
        }
        for price_seed in from_price..from_price + price_limit {
            let world = build_world(price_seed, fund_seed).ok()?;
            let Ok((honest, naive, _, _)) = run_pair(&world) else {
                continue;
            };
            if honest.level == Level::Suspicious
                && naive.level == Level::Real
                && naive.numbers.sharpe > honest.numbers.sharpe
            {
                return Some(StorySeed {
                    price_seed,
                    fund_seed,
                    honest_sharpe: honest.numbers.sharpe,
                    naive_sharpe: naive.numbers.sharpe,
                });
            }
        }
    }
    None
}

// -- the printing ----------------------------------------------------------

/// Terminal color, only when stdout is a terminal and NO_COLOR is unset.
/// The palette is the project's: lavender for structure, green for REAL,
/// crimson for leaks and FANTASY, amber for SUSPICIOUS.
pub struct Painter {
    on: bool,
}

impl Painter {
    pub fn new() -> Painter {
        Painter {
            on: std::io::stdout().is_terminal() && std::env::var_os("NO_COLOR").is_none(),
        }
    }

    fn paint(&self, code: &str, text: &str) -> String {
        if self.on {
            format!("\x1b[{code}m{text}\x1b[0m")
        } else {
            text.to_string()
        }
    }

    pub fn lavender(&self, text: &str) -> String {
        self.paint("38;2;183;126;220", text)
    }

    pub fn bold_lavender(&self, text: &str) -> String {
        self.paint("1;38;2;183;126;220", text)
    }

    pub fn level(&self, level: Level) -> String {
        let code = match level {
            Level::Real => "38;2;96;211;148",
            Level::Suspicious => "38;2;222;170;60",
            Level::Fantasy => "38;2;203;72;88",
        };
        self.paint(code, level.as_str())
    }

    pub fn crimson(&self, text: &str) -> String {
        self.paint("38;2;203;72;88", text)
    }

    pub fn dim(&self, text: &str) -> String {
        self.paint("2", text)
    }
}

impl Default for Painter {
    fn default() -> Painter {
        Painter::new()
    }
}

fn print_verdict_row(name: &str, level: Level, sharpe: f64, dsr_prob: f64, p: f64) {
    println!(
        "  {:<8} {:<11} sharpe {:>6.2}   dsr_prob {:.3}   p {:.3}",
        name,
        level.as_str(),
        sharpe,
        dsr_prob,
        p
    );
}

/// The exact closing line — the demo's contract with every reader.
pub const CLOSING_LINE: &str = "The naive strategy made more money. It was reading a \
number the world didn't have yet. That is the whole problem this library exists for.";

/// Everything the story computed, for tests and callers that want the
/// numbers without the prose.
pub struct DemoResult {
    pub honest: Verdict,
    pub naive: Verdict,
    pub receipt: LeakReceipt,
    pub split_caught: bool,
    pub original_value: f64,
    pub restated_value: f64,
}

/// Run the full narrative, printing it as it goes.
pub fn run() -> Result<DemoResult, DemoError> {
    let paint = Painter::new();
    let world = build_world(PRICE_SEED, FUND_SEED)?;
    let (original, restated) = restatement(&world.funds)?;
    let original_value = original
        .value
        .as_num()
        .ok_or(DemoError::MissingCrime("a numeric original filing"))?;
    let restated_value = restated
        .value
        .as_num()
        .ok_or(DemoError::MissingCrime("a numeric restatement"))?;

    println!(
        "{}",
        paint.bold_lavender(
            "nohindsight demo — is your strategy real, or did you cheat by accident?"
        )
    );
    println!();

    // 1. the world, with its crimes
    println!(
        "Step 1. Built a synthetic market: {} trading days for SYM1..SYM8 (seed {}), and \
         quarterly fundamentals for 2022-2023 (seed {}).  Four data crimes planted in the \
         prices; one restatement planted in the fundamentals.",
        N_DAYS, PRICE_SEED, FUND_SEED
    );
    println!(
        "        The crime that matters here: {GATED} filed ${original_value:.1}m for its \
         last quarter, knowable {} — then re-filed ${restated_value:.1}m (30% lower), \
         knowable {}.",
        original.knowledge_time.to_date_iso(),
        restated.knowledge_time.to_date_iso()
    );

    // 2. the data police over SYM3
    let rows = checks::rows_for_key(&world.prices, "SYM3");
    let findings = checks::run_all(&rows);
    let split_caught = findings
        .iter()
        .any(|f| f.code == "SPLIT_SUSPECTED" && f.severity == Severity::Leak);
    println!();
    println!("Step 2. The data police read SYM3's bars:");
    for line in checks::summarize(&findings).lines() {
        println!("        {}", paint.dim(line));
    }
    if split_caught {
        println!("        The split was caught: SYM3's raw 4:1 overnight drop on 5x volume.");
    } else {
        println!(
            "        {}",
            paint.crimson("WARNING: the split was NOT caught this run — check the seeds.")
        );
    }

    // 3. the two strategies, one engine, one difference
    let receipt = leak_receipt(&world.funds, &world.stamps)?;
    let (verdict_h, verdict_n, engine_h, engine_n) = run_pair(&world)?;
    println!();
    println!(
        "Step 3. Two strategies, one replay engine, same fees, same starting cash, same \
         rule.  The only difference is how they read {GATED}'s revenue:"
    );
    println!("          honest — through the AsOfView: only what the world had published.");
    println!(
        "          naive  — Ledger::latest(key) with no clock: today's terminal number, \
         merged onto every historical date.  Every beginner's pandas merge does this \
         silently."
    );
    println!(
        "        The gate: hold {GATED} while quarterly revenue >= ${FLOOR:.0}m, park in \
         {PARK} otherwise."
    );
    println!(
        "        honest held {GATED} for {} days, then the restatement landed ({}) and it \
         rotated into {PARK}.",
        engine_h.hold_days,
        restated.knowledge_time.to_date_iso()
    );
    if engine_n.hold_days == 0 {
        println!(
            "        naive never held {GATED}: the terminal number (${restated_value:.1}m) \
             was below the bar from day one."
        );
    } else {
        println!(
            "        naive held {GATED} for {} days — on the terminal snapshot, which is \
             the leak.",
            engine_n.hold_days
        );
    }

    // 4. forensics on both streams
    println!();
    println!("Step 4. Forensics on both return streams (n_trials = 1, seeded):");
    print_verdict_row(
        "honest",
        verdict_h.level,
        verdict_h.numbers.sharpe,
        verdict_h.numbers.dsr_prob,
        verdict_h.numbers.p_value,
    );
    print_verdict_row(
        "naive",
        verdict_n.level,
        verdict_n.numbers.sharpe,
        verdict_n.numbers.dsr_prob,
        verdict_n.numbers.p_value,
    );
    if verdict_n.numbers.sharpe > verdict_h.numbers.sharpe {
        println!(
            "        The naive one is HIGHER.  The forensics department interrogates \
             returns; it cannot see a read."
        );
    } else {
        println!(
            "        {}",
            paint.crimson("WARNING: the inversion did not happen this run — check the seeds.")
        );
    }

    // 5. the leak receipt
    println!();
    let rule = paint.lavender(&format!("{:-<64}", ""));
    println!("{rule}");
    println!(
        "{}",
        paint.bold_lavender(" Leak receipt — the naive read, itemized")
    );
    println!("{rule}");
    println!(" {:<22} {}", "key:", paint.crimson(&receipt.key));
    println!(
        " {:<22} {}",
        "decision days leaked:", receipt.decision_days_leaked
    );
    println!(
        " {:<22} {}",
        "first leak day:",
        receipt.first_leak_day.to_date_iso()
    );
    println!(
        " {:<22} {}",
        "restatement known:",
        receipt.knowledge_time.to_date_iso()
    );
    println!(
        " {:<22} {} days before the world had it, at worst",
        "max days early:", receipt.max_days_early
    );
    println!("{rule}");

    // 6. the closing line, then the honest verdict
    println!();
    println!("{CLOSING_LINE}");
    println!();
    println!("Honest verdict: {}", paint.level(verdict_h.level));
    for reason in &verdict_h.reasons {
        println!("  - {reason}");
    }

    Ok(DemoResult {
        honest: verdict_h,
        naive: verdict_n,
        receipt,
        split_caught,
        original_value,
        restated_value,
    })
}

// -- tests: the demo is a contract, so the tests hold it -------------------

#[cfg(test)]
mod tests {
    use super::*;
    use crate::stats::sharpe;

    #[test]
    fn pinned_fund_seed_straddles_the_floor() {
        let funds = synthetic_fundamentals(8, 8, FUND_SEED).expect("ok");
        let stamps = trading_day_stamps(Time::parse("2024-01-02").expect("anchor"), N_DAYS);
        assert!(
            fund_seed_opens_the_seam(&funds, stamps[0]),
            "FUND_SEED must leave the gate open, then close it"
        );
        let (original, restated) = restatement(&funds).expect("restatement");
        let o = original.value.as_num().expect("num");
        let r = restated.value.as_num().expect("num");
        assert!(o >= FLOOR && r < FLOOR, "original {o} restated {r}");
        assert!((r - o * 0.7).abs() < 0.01, "the restatement is 30% down");
        assert_eq!(
            restated.knowledge_time.diff_nanos(original.knowledge_time),
            21 * 86_400 * NANOS_PER_SEC,
            "re-filed exactly 21 days later"
        );
    }

    #[test]
    fn pinned_seeds_produce_the_inversion() {
        // The story's hinge: naive strictly higher, and the two verdicts
        // land on different sides of the evidence line.
        let world = build_world(PRICE_SEED, FUND_SEED).expect("world");
        let (honest, naive, eh, en) = run_pair(&world).expect("pair");
        assert!(
            naive.numbers.sharpe > honest.numbers.sharpe,
            "honest {:.2} vs naive {:.2}",
            honest.numbers.sharpe,
            naive.numbers.sharpe
        );
        assert_eq!(honest.level, Level::Suspicious, "honest level");
        assert_eq!(naive.level, Level::Real, "naive level");
        assert!(eh.hold_days > 0, "the honest gate must actually open");
        assert_eq!(en.hold_days, 0, "the naive gate must never open");
    }

    #[test]
    fn leak_receipt_counts_the_crime() {
        let world = build_world(PRICE_SEED, FUND_SEED).expect("world");
        let receipt = leak_receipt(&world.funds, &world.stamps).expect("receipt");
        assert_eq!(receipt.key, GATED_REVENUE);
        assert!(receipt.decision_days_leaked > 0);
        assert_eq!(
            receipt.max_days_early,
            receipt.knowledge_time.diff_nanos(receipt.first_leak_day) / (86_400 * NANOS_PER_SEC)
        );
        // Every leaked day is strictly before the world had the number.
        let world_had_it = receipt.knowledge_time;
        assert!(receipt.first_leak_day < world_had_it);
    }

    #[test]
    fn replay_engine_is_deterministic_and_switches() {
        let world = build_world(PRICE_SEED, FUND_SEED).expect("world");
        let clock = Clock::at(world.stamps[0]);
        let view = world.funds.view(&clock);
        let a = replay(
            || view.try_get(GATED_REVENUE).and_then(Value::as_num),
            &world.park,
            &world.gated,
            &world.stamps,
            &clock,
        )
        .expect("ok");
        let clock = Clock::at(world.stamps[0]);
        let view = world.funds.view(&clock);
        let b = replay(
            || view.try_get(GATED_REVENUE).and_then(Value::as_num),
            &world.park,
            &world.gated,
            &world.stamps,
            &clock,
        )
        .expect("ok");
        assert_eq!(a, b, "same seeds, same world, same curve");
        assert_eq!(a.equity_curve.len(), N_DAYS);
        assert!(a.n_switches >= 2, "honest enters and leaves the gate");
        let rets = returns_of(&a.equity_curve);
        assert_eq!(rets.len(), N_DAYS - 1);
        // The Sharpe the audit will see is the Sharpe of this curve.
        let sr = sharpe(&rets, 252).expect("ok");
        assert!(sr.is_finite());
        // Sanity on the cash model: equity never wanders far from the
        // starting cash plus market moves — no negative-black-hole bugs.
        for (_, eq) in &a.equity_curve {
            assert!(*eq > 0.0, "equity went negative: {eq}");
        }
        // A constant-revenue world parks in SYM1 forever.
        let clock = Clock::at(world.stamps[0]);
        let always_none =
            replay(|| None, &world.park, &world.gated, &world.stamps, &clock).expect("ok");
        assert_eq!(always_none.hold_days, 0);
        assert_eq!(always_none.n_switches, 1, "one entry into PARK, then held");
    }

    #[test]
    fn returns_of_a_flat_curve_are_zero() {
        let flat: Vec<(Time, f64)> = (0..5)
            .map(|i| (Time::from_nanos(i * NANOS_PER_SEC), 100.0))
            .collect();
        assert!(returns_of(&flat).iter().all(|r| *r == 0.0));
    }
}
