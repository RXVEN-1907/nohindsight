//! nohindsight synth - a small world with crimes planted in it.
//!
//! Everything here is deterministic given its seed (one [`SplitMix64`] per
//! call, draws in a fixed, documented order - see the DRAW ORDER CONTRACT in
//! [`crate::rng`]), so the demo tells the same story every run and the tests
//! can assert on exact numbers.
//!
//! The prices generator plants four data crimes, one symbol each, and the
//! fundamentals generator plants a restatement.  These are the module's
//! contract with the checks package and the demo - the detectors are
//! supposed to find them, so the numbers are planted exactly, not
//! approximately:
//!
//! * SYM3 - split: one overnight close->close drop of exactly 4:1
//!   (log return = -ln 4) with a 5x volume spike; history is left raw - no
//!   back-adjustment, which is precisely the crime a chart vendor would
//!   have hidden;
//! * SYM5 - gap: nine consecutive trading days missing (exchange closure);
//!   the walk simply pauses and resumes where it left off, like a real tape;
//! * SYM6 - stale bars: five consecutive flat bars at the last honest
//!   close, volume zero - a halted symbol or a dead feed;
//! * SYM7 - duplicate: one trading day appended twice; the second entry is
//!   the correction, knowable the next weekday morning;
//! * SYM2 - restatement (fundamentals): the most recent quarter re-filed
//!   21 days after the original, 30% lower.
//!
//! Bars are stamped 20:00 UTC, 4pm US Eastern, the close the bar
//! summarizes, with event_time == knowledge_time: the stated
//! simplification is that a bar is knowable at its own close.  The crimes
//! need roughly n_days >= 12 to fit; below that each block shrinks to what
//! the timeline allows.

use crate::core::{days_in_month, Bar, Ledger, Time, Value};
use crate::rng::SplitMix64;

/// SYM3's 4:1 split - overnight close drops by exactly this.
pub const SPLIT_RATIO: f64 = 4.0;
/// Split-day volume spike (> the split detector's vol_mult default of 3.0).
pub const SPLIT_VOL_MULT: f64 = 5.0;
/// SYM5's exchange closure, in trading days.
pub const GAP_DAYS: usize = 9;
/// SYM6's dead-feed run of identical closes (> the stale check's run default of 4).
pub const STALE_DAYS: usize = 5;
/// SYM7's correction: close nudged +30bps, volume +10%.
pub const DUP_CORRECTION_FACTOR: f64 = 1.003;

/// The universe, in the order every generator walks it.
pub const SYMBOLS: [&str; 8] = [
    "SYM1", "SYM2", "SYM3", "SYM4", "SYM5", "SYM6", "SYM7", "SYM8",
];

// Fixed calendar anchors - never the wall clock; a generator that depends
// on the wall clock is not reproducible.
const FUNDAMENTALS_LAST_QUARTER_END: (i64, u32) = (2023, 4); // year, quarter
const MACRO_LAST_MONTH_END: (i64, u32) = (2023, 12); // year, month

/// Where each crime lives, clamped so small n_days still works.
///
/// Day 0 stays clean: every crime needs at least one honest bar before it
/// as a witness.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct CrimeLayout {
    pub split_idx: Option<usize>,
    /// (start, length) of SYM5's missing run.
    pub gap: (usize, usize),
    /// (start, length) of SYM6's stale run.
    pub stale: (usize, usize),
    pub dup_idx: Option<usize>,
}

/// See [`CrimeLayout`].
pub fn crime_layout(n: usize) -> CrimeLayout {
    let sub = |a: usize, b: usize| a.saturating_sub(b).max(1);
    let split_idx = if n >= 2 { Some((n / 2).max(1)) } else { None };
    let gap_start = (3 * n / 5).max(1).min(sub(n, 1 + GAP_DAYS));
    let gap_len = GAP_DAYS.min(n.saturating_sub(1 + gap_start));
    let stale_start = (n / 3).max(1).min(sub(n, STALE_DAYS));
    let stale_len = STALE_DAYS.min(n.saturating_sub(stale_start));
    let dup_idx = if n >= 2 {
        Some((2 * n / 3).max(1).min(n - 1))
    } else {
        None
    };
    CrimeLayout {
        split_idx,
        gap: (gap_start, gap_len),
        stale: (stale_start, stale_len),
        dup_idx,
    }
}

/// `n` weekday stamps at 20:00 UTC, first on or after `start`.
pub fn trading_day_stamps(start: Time, n: usize) -> Vec<Time> {
    let mut z = start.date_days();
    let mut out = Vec::with_capacity(n);
    while out.len() < n {
        let day = Time::from_date_days(z);
        if day.weekday() < 5 {
            // 20:00 is 8pm UTC = 4pm US Eastern: the close the bar summarizes.
            out.push(day.with_hms(20, 0, 0).expect("20:00 is a valid time"));
        }
        z += 1;
    }
    out
}

/// 14:00 UTC on the next weekday after `ts` - when a correction lands.
pub fn next_weekday_morning(ts: Time) -> Time {
    let mut z = ts.date_days() + 1;
    loop {
        let day = Time::from_date_days(z);
        if day.weekday() < 5 {
            return day.with_hms(14, 0, 0).expect("14:00 is a valid time");
        }
        z += 1;
    }
}

/// Round to `digits` decimals, half away from zero (Python's `round` is
/// half-to-even; the difference is one ulp of the third decimal and every
/// consumer here compares with tolerance, but the choice is stated).
fn round_to(x: f64, digits: u32) -> f64 {
    let scale = 10f64.powi(digits as i32);
    (x * scale).round() / scale
}

struct SymConfig {
    start: f64,
    drift: f64,
    vol: f64,
    volume_base: f64,
}

fn one_honest_bar(
    prev_close: f64,
    volume_base: f64,
    drift: f64,
    vol: f64,
    rng: &mut SplitMix64,
) -> Bar {
    // Draw order for one honest bar (see the module docs): open, close,
    // high, low, volume - five gauss calls, two uniforms each.
    let open = prev_close * (1.0 + rng.gauss(0.0, 0.3 * vol));
    let close = prev_close * (drift + rng.gauss(0.0, vol)).exp();
    let high = open.max(close) * (1.0 + rng.gauss(0.0, 0.5 * vol).abs());
    let low = open.min(close) * (1.0 - rng.gauss(0.0, 0.5 * vol).abs());
    let volume = volume_base * rng.gauss(0.0, 0.35).exp();
    Bar {
        open,
        high,
        low,
        close,
        volume,
    }
}

/// Daily OHLCV bars for SYM1..SYM8 in a [`Ledger`], with four crimes
/// planted.
///
/// Keys are the symbols; values are [`Value::Bar`]s - the trading day
/// itself is the entry's event_time.  Deterministic given seed; RNG
/// discipline: per-symbol (start, drift, vol, volume base) drawn first in
/// symbol order, then bar-by-bar noise walking dates outer, symbols inner.
///
/// The planted crimes are documented at module level and must not be
/// softened: the split detector, gap/stale/duplicate checks and the demo
/// all depend on finding them.
pub fn synthetic_prices(n_days: usize, seed: u64) -> Result<Ledger, crate::core::LedgerError> {
    let mut ledger = Ledger::new("prices")?;
    if n_days < 1 {
        return Ok(ledger);
    }
    let mut rng = SplitMix64::new(seed);
    let layout = crime_layout(n_days);
    let stamps = trading_day_stamps(Time::parse("2024-01-02").expect("anchor"), n_days);

    let mut cfg: Vec<SymConfig> = Vec::with_capacity(SYMBOLS.len());
    for _ in 0..SYMBOLS.len() {
        cfg.push(SymConfig {
            start: rng.uniform(15.0, 140.0),
            drift: rng.uniform(-4e-4, 8e-4),
            vol: rng.uniform(0.008, 0.028),
            volume_base: rng.uniform(4e5, 5e6),
        });
    }

    let (gap_start, gap_len) = layout.gap;
    let (stale_start, stale_len) = layout.stale;
    let mut prev: Vec<(f64, f64)> = vec![(0.0, 0.0); SYMBOLS.len()]; // (close, volume)

    for (i, ts) in stamps.iter().enumerate() {
        for (s, sym) in SYMBOLS.iter().enumerate() {
            let c = &cfg[s];
            if *sym == "SYM5" && gap_start <= i && i < gap_start + gap_len {
                continue; // exchange closed: no bar, the walk pauses
            }
            let (mut close, mut volume) = if prev[s].0 == 0.0 {
                (c.start, c.volume_base)
            } else {
                prev[s]
            };
            let bar = if *sym == "SYM6" && stale_start <= i && i < stale_start + stale_len {
                // halted: frozen at the last honest close, no volume
                Bar {
                    open: close,
                    high: close,
                    low: close,
                    close,
                    volume: 0.0,
                }
            } else if layout.split_idx == Some(i) && *sym == "SYM3" {
                // The crime: a raw, unadjusted 4:1 split.  The close drops
                // by exactly SPLIT_RATIO overnight on 5x volume; earlier
                // bars stay at their raw (pre-split) levels.
                volume *= SPLIT_VOL_MULT;
                close /= SPLIT_RATIO;
                // Split-day shape: 3 gausses (open, high, low), no
                // close/volume noise - the crime sets those exactly.
                let open = close * (1.0 + rng.gauss(0.0, 0.002));
                let high = open.max(close) * (1.0 + rng.gauss(0.0, 0.002).abs());
                let low = open.min(close) * (1.0 - rng.gauss(0.0, 0.002).abs());
                Bar {
                    open,
                    high,
                    low,
                    close,
                    volume,
                }
            } else {
                one_honest_bar(close, c.volume_base, c.drift, c.vol, &mut rng)
            };

            if layout.dup_idx == Some(i) && *sym == "SYM7" {
                // The duplicate: a correction appended the same trading day,
                // knowable the next weekday morning.  Between the close and
                // that morning, the view still shows the raw bar - exactly
                // the revisioning story the ledger exists to tell.
                let mut correction = bar;
                correction.close *= DUP_CORRECTION_FACTOR;
                correction.high = correction.high.max(correction.close);
                correction.low = correction.low.min(correction.close);
                correction.volume *= 1.1;
                ledger.append(sym, Value::Bar(correction), *ts, next_weekday_morning(*ts))?;
            }

            ledger.append(sym, Value::Bar(bar), *ts, *ts)?;
            prev[s] = (bar.close, bar.volume);
        }
    }
    Ok(ledger)
}

/// The `count` calendar-quarter ends ending 2023-12-31, ascending.
fn quarter_ends(count: usize) -> Vec<Time> {
    let mut out = Vec::with_capacity(count);
    let (mut y, mut q) = FUNDAMENTALS_LAST_QUARTER_END;
    for _ in 0..count {
        let month = 3 * q;
        out.push(Time::from_date_days(days_from_ymd(
            y,
            month,
            days_in_month(y, month),
        )));
        if q == 1 {
            q = 4;
            y -= 1;
        } else {
            q -= 1;
        }
    }
    out.reverse();
    out
}

fn days_from_ymd(y: i64, m: u32, d: u32) -> i64 {
    crate::core::days_from_civil(y, m, d)
}

/// Quarterly revenue in millions as reported, with one restatement.
///
/// Key model: one key per symbol, `"<SYM>.revenue"` - each quarter appends a
/// new revision, because that is how a "current fundamentals" feed actually
/// behaves: the latest reported number moves forward each quarter, and
/// which number you were allowed to see is a function of when you looked.
/// event_time = quarter end; knowledge_time = quarter end plus a drawn
/// filing lag of 38-52 days (~45, per-symbol per-quarter).
///
/// The crime: SYM2's most recent quarter gets restated - a further revision
/// exactly 21 days after the original filing, value 30% lower.  Until that
/// 21-day mark the honest view still shows the original number; the naive
/// view (reading today's ledger) never saw the original at all.
/// Deterministic given seed.  Quarters end 2022-03-31 .. 2023-12-31.
pub fn synthetic_fundamentals(
    symbols: usize,
    quarters: usize,
    seed: u64,
) -> Result<Ledger, crate::core::LedgerError> {
    let mut ledger = Ledger::new("fundamentals")?;
    if symbols < 1 || quarters < 1 {
        return Ok(ledger);
    }
    let mut rng = SplitMix64::new(seed);
    let ends = quarter_ends(quarters);

    // Per-symbol (base level, quarterly growth), drawn in symbol order.
    let mut params: Vec<(f64, f64)> = Vec::with_capacity(symbols);
    for _ in 0..symbols {
        params.push((rng.uniform(40.0, 400.0), rng.uniform(0.98, 1.09)));
    }
    let mut level: Vec<f64> = params.iter().map(|(base, _)| *base).collect();

    let last_end = ends[ends.len() - 1];
    let mut sym2_original: Option<(f64, Time)> = None;
    for qe in &ends {
        for (s, (_, growth)) in params.iter().enumerate() {
            let value = round_to(level[s] * (1.0 + rng.gauss(0.0, 0.02)), 3);
            let kt = qe.plus_days_f64(rng.uniform(38.0, 52.0))?;
            let key = format!("SYM{}.revenue", s + 1);
            ledger.append(&key, Value::Num(value), *qe, kt)?;
            level[s] *= growth;
            if s == 1 && *qe == last_end {
                sym2_original = Some((value, kt));
            }
        }
    }

    if let Some((value, kt)) = sym2_original {
        if symbols >= 2 {
            // The restatement: same period, later knowledge, lower number.
            ledger.append(
                "SYM2.revenue",
                Value::Num(round_to(value * 0.7, 3)),
                last_end,
                kt.plus_days(21)?,
            )?;
        }
    }
    Ok(ledger)
}

/// The `count` month ends ending 2023-12-31, ascending.
fn month_ends(count: usize) -> Vec<Time> {
    let mut out = Vec::with_capacity(count);
    let (mut y, mut m) = MACRO_LAST_MONTH_END;
    for _ in 0..count {
        out.push(Time::from_date_days(days_from_ymd(
            y,
            m,
            days_in_month(y, m),
        )));
        if m == 1 {
            m = 12;
            y -= 1;
        } else {
            m -= 1;
        }
    }
    out.reverse();
    out
}

/// Monthly macro values with two vintages per month.
///
/// Key = the series id.  event_time = month end (the period the number
/// describes).  Two vintages per month: the first estimate is knowable at
/// month end + 7 days, the revised estimate at + 38 days, revisions landing
/// between 0.2 and 0.6 points in either direction.  A strategy that reads
/// today's revised series for a past month is reading a number nobody in
/// that month had.  Deterministic given seed; months end 2018-01-31 ..
/// 2023-12-31 for the default years=6.
pub fn synthetic_macro(
    series: &str,
    years: usize,
    seed: u64,
) -> Result<Ledger, crate::core::LedgerError> {
    let mut ledger = Ledger::new("macro")?;
    if years < 1 {
        return Ok(ledger);
    }
    let mut rng = SplitMix64::new(seed);
    let mut level = rng.uniform(3.5, 6.5);
    for me in month_ends(years * 12) {
        level = (level + rng.gauss(0.0, 0.25)).clamp(2.0, 15.0);
        let first = round_to(level, 1);
        let sign = if rng.below(0.5) { 1.0 } else { -1.0 };
        let revised = round_to(first + sign * rng.uniform(0.2, 0.6), 1);
        ledger.append(series, Value::Num(first), me, me.plus_days(7)?)?;
        ledger.append(series, Value::Num(revised), me, me.plus_days(38)?)?;
    }
    Ok(ledger)
}

/// Bars for one ledger key as (event_time, bar) pairs, sorted by
/// (event_time, knowledge_time) - the shape the replay engine and the
/// checks police consume.  Corrections ride alongside their original day.
pub fn bar_history(ledger: &Ledger, key: &str) -> Vec<(Time, Bar)> {
    let mut out: Vec<(Time, Bar)> = ledger
        .history(key)
        .iter()
        .filter_map(|e| e.value.as_bar().map(|b| (e.event_time, b)))
        .collect();
    out.sort_by_key(|(ts, _)| *ts);
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The seed-independent anatomy: 750 days puts the split at index 375,
    /// the SYM5 gap at 450..459, the SYM6 stale run at 250..255 and the
    /// SYM7 duplicate at 500 - asserted by the integration tests too, but
    /// pinned here where the layout lives.
    #[test]
    fn layout_matches_the_documented_plants() {
        let l = crime_layout(750);
        assert_eq!(l.split_idx, Some(375));
        assert_eq!(l.gap, (450, GAP_DAYS));
        assert_eq!(l.stale, (250, STALE_DAYS));
        assert_eq!(l.dup_idx, Some(500));
        // Degenerate lengths degrade gracefully instead of panicking.
        let small = crime_layout(12);
        assert_eq!(small.split_idx, Some(6));
        assert!(small.gap.1 <= GAP_DAYS && small.stale.1 <= STALE_DAYS);
        assert_eq!(crime_layout(0).split_idx, None);
    }

    #[test]
    fn stamps_skip_weekends_and_land_at_the_close() {
        let stamps = trading_day_stamps(Time::parse("2024-01-02").expect("anchor"), 10);
        assert_eq!(stamps.len(), 10);
        assert_eq!(stamps[0].to_iso(), "2024-01-02T20:00:00+00:00");
        // 10 weekday stamps span 13 calendar days (two weekend days out,
        // plus the days themselves: Tue Jan 2 .. Mon Jan 15).
        assert_eq!(stamps[9].date_days() - stamps[0].date_days(), 13);
        for stamp in &stamps {
            assert!(stamp.weekday() < 5);
        }
    }

    #[test]
    fn quarter_and_month_ends_descend_correctly() {
        let ends = quarter_ends(8);
        assert_eq!(ends.len(), 8);
        assert_eq!(ends[0].to_date_iso(), "2022-03-31");
        assert_eq!(ends[7].to_date_iso(), "2023-12-31");
        assert_eq!(ends[6].to_date_iso(), "2023-09-30");
        let months = month_ends(24);
        assert_eq!(months[0].to_date_iso(), "2022-01-31");
        assert_eq!(months[23].to_date_iso(), "2023-12-31");
    }

    #[test]
    fn fundamentals_are_deterministic_and_keyed_per_symbol() {
        let a = synthetic_fundamentals(8, 8, 7).expect("ok");
        let b = synthetic_fundamentals(8, 8, 7).expect("ok");
        assert_eq!(a.len(), b.len());
        for (x, y) in a.entries().iter().zip(b.entries()) {
            assert_eq!(x, y);
        }
        assert!(a.history("SYM2.revenue").len() >= 8);
        assert!(a.history("SYM8.revenue").len() == 8);
    }
}
