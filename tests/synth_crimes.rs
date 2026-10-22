//! The planted crimes, pinned exactly: the split is exactly 4:1 on 5x
//! volume, the gap is exactly 9 trading days, the stale run exactly 5
//! flat bars, the duplicate is a next-morning correction, and the
//! restatement re-files 30% down 21 days later.

use nh::checks::{self, Severity};
use nh::core::{Ledger, Time, Value};
use nh::demo::{FUND_SEED, GATED_REVENUE, N_DAYS, PARK, PRICE_SEED};
use nh::synth::{
    bar_history, crime_layout, next_weekday_morning, synthetic_fundamentals, synthetic_macro,
    synthetic_prices, trading_day_stamps, DUP_CORRECTION_FACTOR, GAP_DAYS, SPLIT_RATIO,
    SPLIT_VOL_MULT, STALE_DAYS, SYMBOLS,
};

fn t(s: &str) -> Time {
    Time::parse(s).expect("valid time")
}

fn price_ledger() -> Ledger {
    synthetic_prices(N_DAYS, PRICE_SEED).expect("ok")
}

#[test]
fn the_split_is_planted_exactly() {
    let ledger = price_ledger();
    let layout = crime_layout(N_DAYS);
    let split_idx = layout.split_idx.expect("750 days plants a split");
    let bars = bar_history(&ledger, "SYM3");
    assert_eq!(bars.len(), N_DAYS, "SYM3 has a bar every day");
    // The crime: close drops by exactly 4:1 overnight on 5x volume.
    let (_, prev) = bars[split_idx - 1];
    let (_, cur) = bars[split_idx];
    assert!((cur.close * SPLIT_RATIO - prev.close).abs() < 1e-9 * prev.close);
    assert!((cur.volume / prev.volume - SPLIT_VOL_MULT).abs() < 1e-9);
    // The walk resumes from the SPLIT close, not the pre-split one.
    let next = bars[split_idx + 1].1.close;
    assert!(next < prev.close, "post-split levels stay post-split");
    // Earlier bars are raw: the crime is unadjusted, like a chart vendor's
    // worst day.
    let before = bars[..split_idx - 1]
        .iter()
        .all(|(_, b)| b.close > cur.close * 2.0);
    assert!(before, "pre-split bars stay at raw (pre-split) levels");
    // And the police catch it.
    let rows = checks::rows_for_key(&ledger, "SYM3");
    let findings = checks::run_all(&rows);
    assert!(findings
        .iter()
        .any(|f| f.code == "SPLIT_SUSPECTED" && f.severity == Severity::Leak));
}

#[test]
fn the_gap_is_planted_exactly() {
    let ledger = price_ledger();
    let layout = crime_layout(N_DAYS);
    let (gap_start, gap_len) = layout.gap;
    assert_eq!(gap_len, GAP_DAYS);
    let stamps = trading_day_stamps(t("2024-01-02"), N_DAYS);
    let bars = bar_history(&ledger, "SYM5");
    assert_eq!(
        bars.len(),
        N_DAYS - GAP_DAYS,
        "nine days simply do not exist"
    );
    // The missing days are exactly the documented window, and the walk
    // resumes where it left off (same close trend, no fabrication).
    let present: std::collections::BTreeSet<i64> =
        bars.iter().map(|(ts, _)| ts.date_days()).collect();
    for (i, stamp) in stamps.iter().enumerate() {
        let should_exist = !(gap_start <= i && i < gap_start + gap_len);
        assert_eq!(
            present.contains(&stamp.date_days()),
            should_exist,
            "day index {i}"
        );
    }
    // The gap check trips WARN over the hole; the weekend around it does not.
    let rows = checks::rows_for_key(&ledger, "SYM5");
    let gaps = checks::check_gaps(&rows, 5);
    assert_eq!(gaps.len(), 1, "one finding for the one closure");
    assert_eq!(gaps[0].severity, Severity::Warn);
}

#[test]
fn the_stale_run_is_planted_exactly() {
    let ledger = price_ledger();
    let layout = crime_layout(N_DAYS);
    let (stale_start, stale_len) = layout.stale;
    assert_eq!(stale_len, STALE_DAYS);
    let bars = bar_history(&ledger, "SYM6");
    // Five consecutive flat bars at the last honest close, volume zero.
    for (_, b) in &bars[stale_start..stale_start + stale_len] {
        assert_eq!(b.open, b.close);
        assert_eq!(b.high, b.close);
        assert_eq!(b.low, b.close);
        assert_eq!(b.volume, 0.0);
    }
    // It is exactly five: the run starts at the LAST honest close (the
    // witness bar before it closes at the same price, by design — the feed
    // froze THERE), and the bar after the run moves again.
    assert_eq!(
        bars[stale_start - 1].1.close,
        bars[stale_start].1.close,
        "the stale run repeats the last honest close"
    );
    assert_ne!(
        bars[stale_start + stale_len - 1].1.close,
        bars[stale_start + stale_len].1.close
    );
    // The stale check sees SIX identical closes: the five frozen bars plus
    // the witness bar before them, which closed at the same price by
    // design (the feed froze there).  One maximal run, one finding.
    let rows = checks::rows_for_key(&ledger, "SYM6");
    let found = checks::check_stale(&rows, 4);
    assert_eq!(found.len(), 1);
    assert!(
        found[0].message.contains("6 consecutive identical closes"),
        "witness + 5 frozen: {}",
        found[0].message
    );
}

#[test]
fn the_duplicate_is_a_next_morning_correction() {
    let ledger = price_ledger();
    let layout = crime_layout(N_DAYS);
    let dup_idx = layout.dup_idx.expect("750 days plants a duplicate");
    let stamps = trading_day_stamps(t("2024-01-02"), N_DAYS);
    let history = ledger.history("SYM7");
    // Two entries share the duplicated day's event_time: the raw bar
    // (knowable at its close) and the correction (knowable the next
    // weekday morning).  Group by calendar day and expect exactly one
    // doubled instant, sitting where the layout says it does.
    let mut by_ts: std::collections::BTreeMap<i64, Vec<&nh::core::Entry>> =
        std::collections::BTreeMap::new();
    for entry in history {
        by_ts
            .entry(entry.event_time.date_days())
            .or_default()
            .push(entry);
    }
    let duplicated: Vec<(&i64, &Vec<&nh::core::Entry>)> =
        by_ts.iter().filter(|(_, v)| v.len() > 1).collect();
    assert_eq!(duplicated.len(), 1, "exactly one duplicated instant");
    let (day, pair) = duplicated[0];
    assert_eq!(*day, stamps[dup_idx].date_days());
    assert_eq!(pair.len(), 2);
    let (raw, correction) = (pair[0], pair[1]); // sorted by (kt, rev): raw first
    assert!(
        correction.knowledge_time > raw.knowledge_time,
        "the correction is knowable later"
    );
    // The correction is the raw bar with the close nudged +30bps and the
    // volume +10% — the same numbers the module's constants name.
    let raw_bar = raw.value.as_bar().expect("bar");
    let fix_bar = correction.value.as_bar().expect("bar");
    assert!((fix_bar.close / raw_bar.close - DUP_CORRECTION_FACTOR).abs() < 1e-12);
    assert!((fix_bar.volume / raw_bar.volume - 1.1).abs() < 1e-12);
    // And the knowledge time is 14:00 UTC on the next weekday.
    assert_eq!(
        correction.knowledge_time,
        next_weekday_morning(raw.event_time)
    );
    // The duplicate detector sees the doubled instant.
    let rows = checks::rows_for_key(&ledger, "SYM7");
    let found = checks::check_duplicates(&rows);
    assert_eq!(found.len(), 1);
    assert_eq!(found[0].code, "DUPLICATE_TS");
}

#[test]
fn the_restatement_is_planted_exactly() {
    let funds = synthetic_fundamentals(8, 8, FUND_SEED).expect("ok");
    let history = funds.history(GATED_REVENUE);
    assert_eq!(history.len(), 9, "eight quarters plus one restatement");
    let original = &history[history.len() - 2];
    let restated = &history[history.len() - 1];
    let o = original.value.as_num().expect("num");
    let r = restated.value.as_num().expect("num");
    // 30% lower, exactly, to the rounding the generator names.
    assert!((r - (o * 0.7_f64).round_to_3()).abs() < 5e-4, "o={o} r={r}");
    // Same quarter, knowledge 21 days later.
    assert_eq!(original.event_time, restated.event_time);
    assert_eq!(
        restated.knowledge_time.diff_nanos(original.knowledge_time),
        21 * 86_400 * nh::core::NANOS_PER_SEC
    );
    assert!(restated.revision > original.revision, "a later revision");
}

trait Round3 {
    fn round_to_3(self) -> f64;
}
impl Round3 for f64 {
    fn round_to_3(self) -> f64 {
        (self * 1000.0).round() / 1000.0
    }
}

#[test]
fn same_seed_same_world_and_macro_carries_two_vintages() {
    let a = price_ledger();
    let b = synthetic_prices(N_DAYS, PRICE_SEED).expect("ok");
    assert_eq!(a.len(), b.len());
    for (x, y) in a.entries().iter().zip(b.entries()) {
        assert_eq!(x, y, "determinism: same seed, same world");
    }
    // A different seed moves the weather (but never the crimes' anatomy).
    let c = synthetic_prices(N_DAYS, PRICE_SEED + 1).expect("ok");
    assert_ne!(a.entries()[0].value, c.entries()[0].value);

    // Macro: two vintages per month — first at +7 days, revised at +38.
    // The ledger sorts by knowledge time, so vintages from adjacent months
    // interleave; group by event_time to check the pairs.
    let macro_ledger = synthetic_macro("UNRATE", 2, 11).expect("ok");
    let history = macro_ledger.history("UNRATE");
    assert_eq!(history.len(), 48, "2 years = 24 months x 2 vintages");
    let mut by_month: std::collections::BTreeMap<i64, Vec<&nh::core::Entry>> =
        std::collections::BTreeMap::new();
    for entry in history {
        by_month
            .entry(entry.event_time.date_days())
            .or_default()
            .push(entry);
    }
    assert_eq!(by_month.len(), 24, "one instant per month end");
    for (day, pair) in &by_month {
        assert_eq!(pair.len(), 2, "month {day}: two vintages");
        let (first, revised) = (pair[0], pair[1]); // (kt, rev) order
        assert_eq!(first.event_time, revised.event_time);
        assert_eq!(
            revised.knowledge_time.diff_nanos(first.knowledge_time),
            31 * 86_400 * nh::core::NANOS_PER_SEC,
            "38-day revised vintage minus 7-day first estimate"
        );
        assert_ne!(
            first.value.as_num().expect("num"),
            revised.value.as_num().expect("num"),
            "vintages disagree by construction"
        );
    }
    // Reading the revised vintage for a past month is reading a number
    // nobody in that month had — the view at +10 days shows only the first.
    let mid = history[0].event_time.plus_days(10).expect("in range");
    assert!(matches!(
        macro_ledger.value_at("UNRATE", mid),
        Ok(Value::Num(_))
    ));
}

#[test]
fn the_demo_symbols_all_exist_with_full_histories() {
    let ledger = price_ledger();
    for sym in SYMBOLS {
        assert!(!ledger.history(sym).is_empty(), "{sym} has bars");
    }
    // SYM1 and SYM2 (the demo's PARK and GATED) are crime-free: a bar for
    // every stamp, no duplicates.
    let stamps = trading_day_stamps(t("2024-01-02"), N_DAYS);
    for sym in [PARK, "SYM2"] {
        let bars = bar_history(&ledger, sym);
        assert_eq!(bars.len(), stamps.len());
        let unique: std::collections::BTreeSet<i64> =
            bars.iter().map(|(ts, _)| ts.date_days()).collect();
        assert_eq!(unique.len(), stamps.len(), "{sym} has no duplicate days");
    }
    // Every bar's geometry holds for the crime-free names.
    for sym in [PARK, "SYM2"] {
        for (_, b) in bar_history(&ledger, sym) {
            assert!(b.low > 0.0 && b.high >= b.open.max(b.close));
            assert!(b.low <= b.open.min(b.close));
            assert!(b.volume > 0.0);
        }
    }
}
