//! nohindsight engine CLI — clap-free argparse by hand.
//!
//! Four doors into the library:
//!
//! ```text
//! nh version                   print the version
//! nh demo                      the full story: planted crimes, two strategies,
//!                              one leak, two verdicts
//! nh audit <csv> [--trials N]  interrogate a one-column returns CSV
//! nh check <csv>               run the data police over a bar CSV
//! ```
//!
//! `main` is the whole contract: 0 means the command ran and found nothing
//! disqualifying, 1 means it ran and found leaks (`check`) or a FANTASY
//! verdict (`audit`), 2 means the input itself was unusable.  All printing
//! lives here and in demo — the library stays silent.
//!
//! The `check` CSV needs a header row: `symbol,ts,open,high,low,close[,volume]
//! [,knowledge]` — `key` is accepted for symbol, `event`/`event_time` for ts,
//! and a missing `knowledge` column means "bars are knowable at their own
//! close", the same default the Python ingest uses.

#![deny(warnings)]

use nh::checks::{self, Severity};
use nh::core::{Bar, Time};
use nh::csv;
use nh::demo;
use nh::forensics::{audit, AuditConfig, Level};
use nh::VERSION;
use std::collections::BTreeMap;
use std::fs;

const EXIT_OK: u8 = 0;
const EXIT_LEAKS: u8 = 1;
const EXIT_BAD_INPUT: u8 = 2;

const USAGE: &str = "nh — nohindsight engine. Python is the research API; Rust is the engine.

usage:
  nh version                      print the version
  nh demo                         the full story: planted crimes, two strategies,
                                  one leak, two verdicts
  nh audit <csv> [--trials N]     interrogate a one-column returns CSV
  nh check <csv>                  run the data police over a bar CSV

check CSV: header row required — symbol,ts,open,high,low,close[,volume][,knowledge];
a missing knowledge column means bars are knowable at their own close.
audit CSV: one column of per-period returns (header optional).

exit codes: 0 clean · 1 leaks or a FANTASY verdict · 2 bad input";

fn print_usage_and_exit() -> u8 {
    eprintln!("{USAGE}");
    EXIT_BAD_INPUT
}

fn main() -> std::process::ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let code = match args.first().map(String::as_str) {
        Some("version") => cmd_version(),
        Some("demo") => cmd_demo(),
        Some("audit") => cmd_audit(&args[1..]),
        Some("check") => cmd_check(&args[1..]),
        Some(_) | None => print_usage_and_exit(),
    };
    std::process::ExitCode::from(code)
}

// -- version ---------------------------------------------------------------

fn cmd_version() -> u8 {
    println!("nh {VERSION}");
    EXIT_OK
}

// -- demo ------------------------------------------------------------------

fn cmd_demo() -> u8 {
    match demo::run() {
        Ok(_) => EXIT_OK,
        Err(e) => {
            eprintln!("error: {e}");
            EXIT_BAD_INPUT
        }
    }
}

// -- audit -----------------------------------------------------------------

/// One column of per-period returns; headerless CSVs just work.  The column
/// candidates and the error shapes mirror the Python CLI's.
fn read_returns_csv(path: &str) -> Result<Vec<f64>, String> {
    let text = fs::read_to_string(path).map_err(|e| format!("{path}: {e}"))?;
    let rows: Vec<Vec<String>> = csv::parse(&text)
        .into_iter()
        .filter(|r| r.iter().any(|c| !c.trim().is_empty()))
        .collect();
    if rows.is_empty() {
        return Err(format!("{path}: no rows"));
    }
    let (start, col) = if rows[0].first().is_some_and(|c| csv::cell_f64(c).is_ok()) {
        (0usize, 0usize)
    } else {
        let header: Vec<String> = rows[0]
            .iter()
            .map(|h| h.trim().to_ascii_lowercase())
            .collect();
        const CANDIDATES: [&str; 5] = ["return", "returns", "ret", "r", "value"];
        let found = CANDIDATES
            .iter()
            .find_map(|c| header.iter().position(|h| h == c));
        let col = match found {
            Some(col) => col,
            None if header.len() == 1 => 0,
            None => {
                return Err(format!(
                    "{path}: {} columns, none named return/returns/ret/r/value — \
                     pass a one-column CSV",
                    header.len()
                ))
            }
        };
        (1, col)
    };
    let mut returns = Vec::new();
    for (lineno, row) in rows.iter().enumerate().skip(start) {
        let cell = row.get(col).map(|s| s.trim()).unwrap_or("");
        if cell.is_empty() {
            continue;
        }
        match csv::cell_f64(cell) {
            Ok(v) => returns.push(v),
            Err(_) => {
                return Err(format!(
                    "{path}: line {}: {cell:?} is not a number",
                    lineno + 1
                ))
            }
        }
    }
    if returns.len() < 2 {
        return Err(format!(
            "{path}: need at least 2 returns, got {}",
            returns.len()
        ));
    }
    Ok(returns)
}

fn cmd_audit(args: &[String]) -> u8 {
    let mut path: Option<&String> = None;
    let mut n_trials: usize = 1;
    let mut i = 0usize;
    while i < args.len() {
        match args[i].as_str() {
            "--trials" => {
                let Some(value) = args.get(i + 1) else {
                    eprintln!("error: --trials needs a number");
                    return EXIT_BAD_INPUT;
                };
                match value.parse::<usize>() {
                    Ok(n) if n >= 1 => n_trials = n,
                    _ => {
                        eprintln!("error: --trials must be a positive integer, got {value:?}");
                        return EXIT_BAD_INPUT;
                    }
                }
                i += 2;
            }
            other if other.starts_with("--") => {
                eprintln!("error: unrecognized flag {other:?}");
                return EXIT_BAD_INPUT;
            }
            _ if path.is_none() => {
                path = Some(&args[i]);
                i += 1;
            }
            _ => {
                eprintln!("error: audit takes one CSV");
                return EXIT_BAD_INPUT;
            }
        }
    }
    let Some(path) = path else {
        eprintln!("error: audit needs a returns CSV");
        return EXIT_BAD_INPUT;
    };

    let returns = match read_returns_csv(path) {
        Ok(r) => r,
        Err(e) => {
            eprintln!("error: {e}");
            return EXIT_BAD_INPUT;
        }
    };

    let verdict = match audit(
        &returns,
        AuditConfig {
            n_trials,
            ..AuditConfig::default()
        },
        None,
    ) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("error: {e}");
            return EXIT_BAD_INPUT;
        }
    };
    let n = verdict.numbers;
    let bar = "=".repeat(64);
    println!("{bar}");
    println!(" nohindsight audit — {path}");
    println!("{bar}");
    println!(" VERDICT: {}", verdict.level);
    println!(
        " sharpe {:.2} \u{b7} dsr_prob {:.3} \u{b7} p {:.3} \u{b7} max_dd {:.1}%",
        n.sharpe,
        n.dsr_prob,
        n.p_value,
        n.max_drawdown * 100.0
    );
    println!(" reasons:");
    for reason in &verdict.reasons {
        println!("  - {reason}");
    }
    println!("{bar}");

    // The audit's exit contract: a FANTASY verdict is the tool telling you
    // the backtest is entertainment — that is a failure to pass, exit 1.
    if verdict.level == Level::Fantasy {
        EXIT_LEAKS
    } else {
        EXIT_OK
    }
}

// -- check -----------------------------------------------------------------

/// The bar CSV's resolved column layout.
struct Columns {
    symbol: usize,
    ts: usize,
    knowledge: Option<usize>,
    open: usize,
    high: usize,
    low: usize,
    close: usize,
    volume: Option<usize>,
}

fn resolve_columns(header: &[String]) -> Result<Columns, String> {
    let lower: Vec<String> = header
        .iter()
        .map(|h| h.trim().to_ascii_lowercase())
        .collect();
    let find = |names: &[&str]| -> Result<usize, String> {
        names
            .iter()
            .find_map(|n| lower.iter().position(|h| h == n))
            .ok_or_else(|| {
                format!(
                    "no column named {}; header was: {}",
                    names.join(" or "),
                    header.join(", ")
                )
            })
    };
    Ok(Columns {
        symbol: find(&["symbol", "key", "sym"])?,
        ts: find(&["ts", "event", "event_time", "date", "time"])?,
        knowledge: lower
            .iter()
            .position(|h| h == "knowledge" || h == "knowledge_time" || h == "kt"),
        open: find(&["open"])?,
        high: find(&["high"])?,
        low: find(&["low"])?,
        close: find(&["close"])?,
        volume: lower.iter().position(|h| h == "volume"),
    })
}

fn cmd_check(args: &[String]) -> u8 {
    if args.len() != 1 || args[0].starts_with("--") {
        eprintln!("error: check takes exactly one bar CSV");
        return EXIT_BAD_INPUT;
    }
    let path = &args[0];
    let text = match fs::read_to_string(path) {
        Ok(t) => t,
        Err(e) => {
            eprintln!("error: {path}: {e}");
            return EXIT_BAD_INPUT;
        }
    };
    let rows = csv::parse(&text);
    if rows.is_empty() {
        eprintln!("error: {path}: no rows");
        return EXIT_BAD_INPUT;
    }
    let columns = match resolve_columns(&rows[0]) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("error: {path}: {e}");
            return EXIT_BAD_INPUT;
        }
    };

    // Group rows by symbol, preserving file order inside each symbol, then
    // sort by (ts, knowledge) — the ordering the checks' contract requires.
    let mut by_symbol: BTreeMap<String, Vec<(Time, Time, Bar)>> = BTreeMap::new();
    for (lineno, row) in rows.iter().enumerate().skip(1) {
        if row.iter().all(|c| c.trim().is_empty()) {
            continue;
        }
        let cell = |idx: usize| -> Result<f64, String> {
            csv::cell_f64(row.get(idx).map(String::as_str).unwrap_or(""))
        };
        let symbol = row
            .get(columns.symbol)
            .map(|s| s.trim().to_string())
            .unwrap_or_default();
        if symbol.is_empty() {
            eprintln!("error: {path}: line {}: empty symbol", lineno + 1);
            return EXIT_BAD_INPUT;
        }
        let ts_text = row.get(columns.ts).map(|s| s.trim()).unwrap_or_default();
        let ts = match Time::parse(ts_text) {
            Ok(t) => t,
            Err(e) => {
                eprintln!("error: {path}: line {}: {e}", lineno + 1);
                return EXIT_BAD_INPUT;
            }
        };
        let parsed: Result<Bar, String> = (|| {
            Ok(Bar {
                open: cell(columns.open)?,
                high: cell(columns.high)?,
                low: cell(columns.low)?,
                close: cell(columns.close)?,
                volume: match columns.volume {
                    Some(v) => cell(v)?,
                    None => 0.0,
                },
            })
        })();
        let bar = match parsed {
            Ok(b) => b,
            Err(e) => {
                eprintln!("error: {path}: line {}: {e}", lineno + 1);
                return EXIT_BAD_INPUT;
            }
        };
        let knowledge = match columns.knowledge {
            Some(k) => match Time::parse(row.get(k).map(String::as_str).unwrap_or("")) {
                Ok(t) => t,
                Err(e) => {
                    eprintln!("error: {path}: line {}: {e}", lineno + 1);
                    return EXIT_BAD_INPUT;
                }
            },
            None => ts, // bars are knowable at their own close, by default
        };
        by_symbol
            .entry(symbol)
            .or_default()
            .push((ts, knowledge, bar));
    }

    let n_symbols = by_symbol.len();
    let mut all_findings: Vec<checks::Finding> = Vec::new();
    for (symbol, mut entries) in by_symbol {
        entries.sort_by(|a, b| a.0.cmp(&b.0).then(a.1.cmp(&b.1)));
        let bar_rows: Vec<checks::BarRow> = entries
            .iter()
            .map(|(ts, _, bar)| checks::BarRow { ts: *ts, bar: *bar })
            .collect();
        let knowledge: Vec<Time> = entries.iter().map(|(_, kt, _)| *kt).collect();
        let mut findings = checks::run_all(&bar_rows);
        findings.extend(checks::check_knowledge_order(&bar_rows, &knowledge));
        for finding in findings {
            all_findings.push(checks::Finding {
                code: finding.code,
                severity: finding.severity,
                // The report layer prefixes the symbol, like the Python CLI.
                message: format!("{symbol} \u{b7} {}", finding.message),
                index: finding.index,
            });
        }
    }

    println!("{}", checks::summarize(&all_findings));
    for finding in &all_findings {
        println!("  {} {}", finding.code, finding.message);
    }
    let leaks = all_findings
        .iter()
        .filter(|f| f.severity == Severity::Leak)
        .count();
    let warns = all_findings
        .iter()
        .filter(|f| f.severity == Severity::Warn)
        .count();
    let infos = all_findings
        .iter()
        .filter(|f| f.severity == Severity::Info)
        .count();
    println!("scanned {n_symbols} symbol(s): {leaks} leaks, {warns} warnings, {infos} info");
    if checks::has_leaks(&all_findings) {
        EXIT_LEAKS
    } else {
        EXIT_OK
    }
}
