//! nohindsight checks — the bar-level police.
//!
//! Conventions every check in here shares:
//!
//! * A *bar row* is a [`BarRow`]: a timestamp plus a [`Bar`] — the checks
//!   never look at anything else.
//! * Rows arrive sorted ascending by ts.  That ordering is the caller's
//!   contract ([`bar_history`], the CLI's check command); checks are pure
//!   functions and never reorder, copy, or mutate.
//! * Every check returns `Vec<Finding>` and takes no clock: these are
//!   properties of the data itself, not of when you looked at it.
//! * One finding per offense site — one bad bar, one duplicated instant,
//!   one stale run, one gap — so a report can point at the wound.
//!
//! This is the compact Rust police: the Python package also ships crossed
//! quotes and a bid/ask column family; the engine covers the structural
//! crimes (OHLC geometry, duplicates, calendar gaps, stale runs, unadjusted
//! splits, scale shocks, impossible knowledge) and says so rather than
//! pretending to be the whole department.

use crate::core::{Ledger, Time};
use crate::synth::bar_history;
use std::collections::BTreeMap;
use std::fmt;

/// How bad a finding is.  LEAK means the data can poison a backtest's PnL;
/// WARN means hold your nose but the arithmetic survives.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Severity {
    Leak,
    Warn,
    Info,
}

impl fmt::Display for Severity {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Severity::Leak => "LEAK",
            Severity::Warn => "WARN",
            Severity::Info => "INFO",
        })
    }
}

/// One offense, at one site, with the message that argues it.
#[derive(Clone, Debug, PartialEq)]
pub struct Finding {
    /// Stable code, e.g. "SPLIT_SUSPECTED" — the same codes the Python
    /// police print.
    pub code: &'static str,
    pub severity: Severity,
    pub message: String,
    /// Index into the checked row slice (or the first row of a range).
    pub index: usize,
}

/// A timestamp plus its bar — one row of a price series.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct BarRow {
    pub ts: Time,
    pub bar: crate::core::Bar,
}

/// Rows (ts + bar) for one ledger key, sorted ascending by event_time.
/// Corrections share their original day's event_time and ride right after
/// it — which is exactly how the duplicate detector sees them.
pub fn rows_for_key(ledger: &Ledger, key: &str) -> Vec<BarRow> {
    bar_history(ledger, key)
        .into_iter()
        .map(|(ts, bar)| BarRow { ts, bar })
        .collect()
}

/// Geometry police: high >= max(open, close), low <= min(open, close),
/// low > 0, all four fields finite.
///
/// Violations are LEAK: data this broken poisons anything built on it.  At
/// most one finding per bar (every problem is listed in the one message).
pub fn check_ohlc(rows: &[BarRow]) -> Vec<Finding> {
    let mut out = Vec::new();
    for (i, row) in rows.iter().enumerate() {
        let b = row.bar;
        let mut problems: Vec<String> = Vec::new();
        if !b.open.is_finite() || !b.high.is_finite() || !b.low.is_finite() || !b.close.is_finite()
        {
            problems.push("a non-finite OHLC value".to_string());
        } else {
            if b.low <= 0.0 {
                problems.push(format!("low {} <= 0", b.low));
            }
            if b.high < b.open.max(b.close) {
                problems.push(format!(
                    "high {} < max(open, close) {}",
                    b.high,
                    b.open.max(b.close)
                ));
            }
            if b.low > b.open.min(b.close) {
                problems.push(format!(
                    "low {} > min(open, close) {}",
                    b.low,
                    b.open.min(b.close)
                ));
            }
        }
        if !problems.is_empty() {
            out.push(Finding {
                code: "OHLC_INVALID",
                severity: Severity::Leak,
                message: format!("bar {i} ({}): {}", row.ts.to_iso(), problems.join("; ")),
                index: i,
            });
        }
    }
    out
}

/// The same instant twice -> LEAK.
///
/// A duplicated timestamp is either a correction appended without
/// revisioning or a feed hiccup; either way the series no longer says one
/// thing per instant.  One finding per duplicated instant (not per pair).
pub fn check_duplicates(rows: &[BarRow]) -> Vec<Finding> {
    let mut first_seen: BTreeMap<Time, usize> = BTreeMap::new();
    let mut dupes: BTreeMap<Time, Vec<usize>> = BTreeMap::new();
    for (i, row) in rows.iter().enumerate() {
        match first_seen.get(&row.ts) {
            Some(first) => {
                dupes.entry(row.ts).or_insert_with(|| vec![*first]).push(i);
            }
            None => {
                first_seen.insert(row.ts, i);
            }
        }
    }
    dupes
        .into_iter()
        .map(|(ts, indices)| Finding {
            code: "DUPLICATE_TS",
            severity: Severity::Leak,
            message: format!(
                "timestamp {} appears {} times (bars {:?}) — one instant, one bar",
                ts.to_iso(),
                indices.len(),
                indices
            ),
            index: indices[0],
        })
        .collect()
}

/// Weekdays d with d1 < d <= d2 (0 when d2 <= d1).
///
/// Plain loop on purpose: calendar gaps are days, not microseconds, and an
/// obviously-correct loop beats a clever remainder formula here.
fn weekdays_between(d1: Time, d2: Time) -> usize {
    let mut n = 0usize;
    let mut z = d1.date_days() + 1;
    let end = d2.date_days();
    while z <= end {
        if Time::from_date_days(z).weekday() < 5 {
            n += 1;
        }
        z += 1;
    }
    n
}

/// Trading-calendar police: an unexplained closure is WARN.
///
/// The gap between consecutive bars is counted in *weekdays* — dates d with
/// earlier.date() < d <= later.date() and d a weekday — so an ordinary
/// weekend (Friday -> Monday) is a gap of 1, same as any other healthy
/// overnight.  A spacing of more than `max_gap_days` weekdays is a WARN:
/// the backtest silently held through days it has no data for.
pub fn check_gaps(rows: &[BarRow], max_gap_days: usize) -> Vec<Finding> {
    let mut out = Vec::new();
    let mut prev: Option<(usize, Time)> = None;
    for (i, row) in rows.iter().enumerate() {
        if let Some((j, prev_ts)) = prev {
            let gap = weekdays_between(prev_ts, row.ts);
            if gap > max_gap_days {
                out.push(Finding {
                    code: "CALENDAR_GAP",
                    severity: Severity::Warn,
                    message: format!(
                        "{} weekdays between bars[{}] ({}) and bars[{}] ({}) — \
                         expected 1; an unexplained closure",
                        gap,
                        j,
                        prev_ts.to_iso(),
                        i,
                        row.ts.to_iso()
                    ),
                    index: j,
                });
            }
        }
        prev = Some((i, row.ts));
    }
    out
}

/// `run` consecutive identical closes -> WARN: a dead feed or a halted
/// symbol.
///
/// Exact equality on close — a stale feed repeats bits, not approximately.
/// One finding per maximal run (six identical closes at run=4 is one
/// finding, not three).
pub fn check_stale(rows: &[BarRow], run: usize) -> Vec<Finding> {
    let mut out = Vec::new();
    let mut i = 0usize;
    while i < rows.len() {
        let mut j = i;
        while j + 1 < rows.len() && rows[j + 1].bar.close == rows[i].bar.close {
            j += 1;
        }
        let length = j - i + 1;
        if length >= run && rows[i].bar.close.is_finite() {
            out.push(Finding {
                code: "STALE_CLOSES",
                severity: Severity::Warn,
                message: format!(
                    "{} consecutive identical closes ({}) from bars[{}] to bars[{}] — \
                     dead feed or halted symbol",
                    length, rows[i].bar.close, i, j
                ),
                index: i,
            });
        }
        i = j + 1;
    }
    out
}

/// Round-ratio landing pads for the split detector, with the match rule:
/// ||r| - ln k| <= max(0.01, 0.02 * ln k).  Round ratios are landing pads,
/// not sniper targets — a 4:1 split with 0.5% of same-day drift must still
/// be caught.
const SPLIT_RATIOS: [f64; 5] = [2.0, 3.0, 4.0, 5.0, 10.0];
const SPLIT_TOL_REL: f64 = 0.02;
const SPLIT_TOL_ABS: f64 = 0.01;

fn matching_ratio(abs_log_return: f64) -> Option<f64> {
    SPLIT_RATIOS.iter().copied().find(|k| {
        let target = k.ln();
        (abs_log_return - target).abs() <= SPLIT_TOL_ABS.max(SPLIT_TOL_REL * target)
    })
}

/// Overnight close-to-close |log return| within tolerance of ln(k) for k in
/// {2, 3, 4, 5, 10}, AND volume ratio > vol_mult -> LEAK finding.
///
/// The volume condition is load-bearing: a crash is not a split.  A bar
/// pair with a non-positive previous volume has no ratio and no verdict —
/// say nothing rather than guess.  Severity is LEAK because an unadjusted
/// split in a backtest produces phantom PnL.
pub fn detect_splits(rows: &[BarRow], vol_mult: f64) -> Vec<Finding> {
    let mut out = Vec::new();
    for i in 1..rows.len() {
        let (prev, cur) = (&rows[i - 1], &rows[i]);
        if prev.bar.close <= 0.0 || !prev.bar.close.is_finite() || !cur.bar.close.is_finite() {
            continue;
        }
        let r = (cur.bar.close / prev.bar.close).ln();
        let Some(k) = matching_ratio(r.abs()) else {
            continue;
        };
        if prev.bar.volume <= 0.0 || !prev.bar.volume.is_finite() || !cur.bar.volume.is_finite() {
            continue;
        }
        let volume_ratio = cur.bar.volume / prev.bar.volume;
        if volume_ratio <= vol_mult {
            continue;
        }
        out.push(Finding {
            code: "SPLIT_SUSPECTED",
            severity: Severity::Leak,
            message: format!(
                "overnight close ratio {:.3} at bars[{i}] ({}) — |log return| {:.3} \
                 lands on ln {k} with {volume_ratio:.1}x volume (> {vol_mult}x): \
                 unadjusted split; raw levels book phantom PnL",
                r.exp(),
                cur.ts.to_iso(),
                r.abs()
            ),
            index: i,
        });
    }
    out
}

/// Scale-shock internals, deliberately not parameters: the public signature
/// is pinned, and these two numbers are judgments, not knobs.
const SHOCK_WINDOW: usize = 30; // trailing daily returns that count as "recent"
const SHOCK_MIN_OBS: usize = 8; // fewer than this and a sigma is a rumor

/// A single-bar move bigger than `z` sigmas of the recent daily-move
/// distribution -> WARN: a candidate split or a bad tick.  This check only
/// raises its hand; the split detector decides which it was.
///
/// Method, documented rather than tunable: returns are log close-to-close;
/// "recent" is the trailing 30 returns before the candidate, sample stdev; a
/// bar is judged only once 8 returns exist before it.  A window with sigma
/// <= 0 (perfectly flat history) judges nothing — a flat window has no
/// scale, and the stale check already flagged it.
pub fn check_scale_shock(rows: &[BarRow], z: f64) -> Vec<Finding> {
    let mut rets: Vec<Option<f64>> = vec![None]; // rets[i] links bars[i-1] -> bars[i]
    for i in 1..rows.len() {
        let (prev, cur) = (rows[i - 1].bar.close, rows[i].bar.close);
        rets.push(if prev > 0.0 && cur > 0.0 {
            Some((cur / prev).ln())
        } else {
            None
        });
    }
    let mut out = Vec::new();
    for i in 1..rows.len() {
        let Some(r) = rets[i] else { continue };
        let window: Vec<f64> = rets[i.saturating_sub(SHOCK_WINDOW)..i]
            .iter()
            .filter_map(|x| *x)
            .collect();
        if window.len() < SHOCK_MIN_OBS {
            continue;
        }
        let mean = window.iter().sum::<f64>() / window.len() as f64;
        let sigma = ((window.iter().map(|x| (x - mean) * (x - mean)).sum::<f64>()
            / (window.len() - 1) as f64)
            .max(0.0))
        .sqrt();
        if sigma <= 0.0 {
            continue;
        }
        let score = r.abs() / sigma;
        if score > z {
            out.push(Finding {
                code: "SCALE_SHOCK",
                severity: Severity::Warn,
                message: format!(
                    "bar {i} ({}): move {r:+.4} is {score:.1} sigmas of the \
                     trailing {SHOCK_WINDOW}-bar distribution (sigma={sigma:.3e}) — \
                     candidate split or bad tick",
                    rows[i].ts.to_iso()
                ),
                index: i,
            });
        }
    }
    out
}

/// A revision whose knowledge_time precedes its event_time is impossible —
/// the world can't know things before they happen.  (The Ledger refuses to
/// build one; a CSV the police are fed can still carry the contradiction,
/// so the police check it.)
pub fn check_knowledge_order(rows: &[BarRow], knowledge: &[Time]) -> Vec<Finding> {
    let mut out = Vec::new();
    for (i, (row, kt)) in rows.iter().zip(knowledge.iter()).enumerate() {
        if *kt < row.ts {
            out.push(Finding {
                code: "KNOWLEDGE_BEFORE_EVENT",
                severity: Severity::Leak,
                message: format!(
                    "bar {i} ({}): knowledge_time {} precedes event_time {} — \
                     the world can't know things before they happen",
                    row.ts.to_iso(),
                    kt.to_iso(),
                    row.ts.to_iso()
                ),
                index: i,
            });
        }
    }
    out
}

/// Every structural check at once, in report order: geometry, duplicates,
/// gaps, stale runs, splits, scale shocks.  (Knowledge order is the CLI's
/// business — the ledger can't hold an impossible revision, a CSV can.)
pub fn run_all(rows: &[BarRow]) -> Vec<Finding> {
    let mut findings = Vec::new();
    findings.extend(check_ohlc(rows));
    findings.extend(check_duplicates(rows));
    findings.extend(check_gaps(rows, 5));
    findings.extend(check_stale(rows, 4));
    findings.extend(detect_splits(rows, 3.0));
    findings.extend(check_scale_shock(rows, 8.0));
    findings
}

/// Any LEAK-severity finding in the report?
pub fn has_leaks(findings: &[Finding]) -> bool {
    findings.iter().any(|f| f.severity == Severity::Leak)
}

/// The text summary: one table of codes, severities and counts — the shape
/// the CLI prints and the demo folds into its story.
pub fn summarize(findings: &[Finding]) -> String {
    if findings.is_empty() {
        return "no findings: the bars are clean".to_string();
    }
    let mut order: Vec<(&'static str, Severity)> = Vec::new();
    let mut counts: BTreeMap<&'static str, usize> = BTreeMap::new();
    for f in findings {
        if !counts.contains_key(&f.code) {
            order.push((f.code, f.severity));
        }
        *counts.entry(f.code).or_insert(0) += 1;
    }
    let code_width = order.iter().map(|(c, _)| c.len()).max().unwrap_or(4);
    let mut lines = Vec::with_capacity(order.len() + 2);
    lines.push(format!(
        "{:<width$}  {:<7}  COUNT",
        "CODE",
        "SEVERITY",
        width = code_width
    ));
    for (code, severity) in order {
        lines.push(format!(
            "{:<width$}  {:<7}  {:>5}",
            code,
            severity.to_string(),
            counts[&code],
            width = code_width
        ));
    }
    lines.join("\n")
}

// The `days in a month` helper stays in `core`; nothing else re-exports it.

#[cfg(test)]
mod tests {
    use super::*;
    use crate::core::Bar;

    fn row(day: i64, close: f64, volume: f64) -> BarRow {
        BarRow {
            ts: Time::from_date_days(day).with_hms(20, 0, 0).expect("ok"),
            bar: Bar {
                open: close,
                high: close * 1.01,
                low: close * 0.99,
                close,
                volume,
            },
        }
    }

    #[test]
    fn split_detector_needs_both_the_ratio_and_the_volume() {
        // A clean tape: no findings.
        let clean: Vec<BarRow> = (0..30).map(|d| row(d, 100.0 + d as f64, 1e6)).collect();
        assert!(detect_splits(&clean, 3.0).is_empty());
        // 4:1 overnight drop on 5x volume: caught.
        let mut split: Vec<BarRow> = clean.clone();
        split.push(row(30, (100.0 + 29.0) / 4.0, 5e6));
        let found = detect_splits(&split, 3.0);
        assert_eq!(found.len(), 1);
        assert_eq!(found[0].code, "SPLIT_SUSPECTED");
        assert_eq!(found[0].severity, Severity::Leak);
        // The same drop WITHOUT the volume spike: a crash, not a split.
        let mut crash: Vec<BarRow> = clean.clone();
        crash.push(row(30, (100.0 + 29.0) / 4.0, 1.1e6));
        assert!(detect_splits(&crash, 3.0).is_empty());
    }

    #[test]
    fn stale_and_duplicate_and_gap_rules() {
        // Five identical closes at volume 0: one stale finding, not five.
        let mut rows: Vec<BarRow> = (0..10).map(|d| row(d, 50.0 + d as f64, 1e6)).collect();
        for d in 10..15 {
            rows.push(row(d, 60.0, 0.0));
        }
        let stale = check_stale(&rows, 4);
        assert_eq!(stale.len(), 1);
        assert!(stale[0].message.contains("5 consecutive"));

        // The same instant twice: one finding naming both indices.
        let dup = vec![row(1, 100.0, 1e6), row(1, 100.3, 1.1e6), row(2, 101.0, 1e6)];
        let found = check_duplicates(&dup);
        assert_eq!(found.len(), 1);
        assert_eq!(found[0].code, "DUPLICATE_TS");

        // A 9-weekday hole (13 calendar days) trips the gap check at 5.
        let gappy = vec![row(1, 100.0, 1e6), row(15, 101.0, 1e6)];
        let gaps = check_gaps(&gappy, 5);
        assert_eq!(gaps.len(), 1);
        assert!(gaps[0].message.contains("weekdays between"));
        // A weekend (Fri -> Mon) does not.
        let fri = Time::parse("2024-01-05T20:00:00").expect("fri");
        let mon = Time::parse("2024-01-08T20:00:00").expect("mon");
        let weekend = vec![
            BarRow {
                ts: fri,
                bar: dup[0].bar,
            },
            BarRow {
                ts: mon,
                bar: dup[2].bar,
            },
        ];
        assert!(check_gaps(&weekend, 5).is_empty());
    }

    #[test]
    fn ohlc_geometry_and_knowledge_order() {
        let bad = BarRow {
            ts: Time::from_date_days(1),
            bar: Bar {
                open: 10.0,
                high: 9.0, // high < max(open, close)
                low: 11.0, // low > min(open, close)
                close: 10.5,
                volume: 1.0,
            },
        };
        let found = check_ohlc(&[bad]);
        assert_eq!(found.len(), 1);
        assert!(found[0].message.contains("high 9 <"));
        assert!(found[0].message.contains("low 11 >"));

        // A revision knowable the day BEFORE its event: impossible.
        let ts = Time::parse("2024-03-01T20:00:00").expect("ts");
        let kt = Time::parse("2024-02-29T20:00:00").expect("kt");
        let r = BarRow { ts, bar: bad.bar };
        let found = check_knowledge_order(&[r], &[kt]);
        assert_eq!(found[0].code, "KNOWLEDGE_BEFORE_EVENT");
        assert_eq!(found[0].severity, Severity::Leak);
    }

    #[test]
    fn summarize_counts_by_code() {
        let findings = vec![
            Finding {
                code: "SPLIT_SUSPECTED",
                severity: Severity::Leak,
                message: "a".to_string(),
                index: 1,
            },
            Finding {
                code: "SCALE_SHOCK",
                severity: Severity::Warn,
                message: "b".to_string(),
                index: 1,
            },
            Finding {
                code: "SPLIT_SUSPECTED",
                severity: Severity::Leak,
                message: "c".to_string(),
                index: 2,
            },
        ];
        let text = summarize(&findings);
        let split_line = text
            .lines()
            .find(|l| l.starts_with("SPLIT_SUSPECTED"))
            .expect("a row per code");
        assert!(split_line.contains("LEAK") && split_line.trim().ends_with('2'));
        let shock_line = text
            .lines()
            .find(|l| l.starts_with("SCALE_SHOCK"))
            .expect("a row per code");
        assert!(shock_line.contains("WARN") && shock_line.trim().ends_with('1'));
        assert!(has_leaks(&findings));
        assert_eq!(summarize(&[]), "no findings: the bars are clean");
        assert!(!has_leaks(&[]));
    }

    #[test]
    fn rows_for_key_orders_a_ledger_key() {
        let mut ledger = Ledger::new("prices").expect("ok");
        let d0 = Time::parse("2024-01-02T20:00:00").expect("d0");
        let d1 = Time::parse("2024-01-03T20:00:00").expect("d1");
        let b0 = Bar {
            open: 1.0,
            high: 1.0,
            low: 1.0,
            close: 1.0,
            volume: 1.0,
        };
        let b1 = Bar {
            open: 2.0,
            high: 2.0,
            low: 2.0,
            close: 2.0,
            volume: 2.0,
        };
        ledger
            .append("X", crate::core::Value::Bar(b1), d1, d1)
            .expect("ok");
        ledger
            .append("X", crate::core::Value::Bar(b0), d0, d0)
            .expect("ok");
        let rows = rows_for_key(&ledger, "X");
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[0].ts, d0);
        assert_eq!(rows[1].ts, d1);
    }
}
