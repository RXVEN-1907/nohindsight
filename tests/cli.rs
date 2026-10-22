//! The binary, end to end: version, the demo story, the audit's exit
//! contract, and the data police over a real CSV.  These run the built
//! binary, so they are the same door a user walks through.

use std::process::Command;

fn nh() -> Command {
    Command::new(env!("CARGO_BIN_EXE_nh"))
}

fn temp_csv(name: &str, body: &str) -> std::path::PathBuf {
    let mut path = std::env::temp_dir();
    path.push(format!("nh_test_{}_{}.csv", name, std::process::id()));
    std::fs::write(&path, body).expect("write temp csv");
    path
}

/// A deterministic pseudo-random stream for crafted CSVs (xorshift32) -
/// tests stay seed-pinned without a rand crate.
struct Xor32(u32);
impl Xor32 {
    fn next_f64(&mut self) -> f64 {
        let mut x = self.0;
        x ^= x << 13;
        x ^= x >> 17;
        x ^= x << 5;
        self.0 = x;
        (x % 10_000) as f64 / 10_000.0
    }
}

fn returns_csv(
    name: &str,
    make: impl Fn(usize, &mut Xor32) -> f64,
    n: usize,
    seed: u32,
) -> std::path::PathBuf {
    let mut rng = Xor32(seed);
    let mut body = String::from("return\n");
    for i in 0..n {
        body.push_str(&format!("{:.6}\n", make(i, &mut rng)));
    }
    temp_csv(name, &body)
}

#[test]
fn version_prints_the_engine_version() {
    let out = nh().arg("version").output().expect("run nh");
    assert!(out.status.success());
    assert_eq!(String::from_utf8_lossy(&out.stdout).trim(), "nh 0.1.0");
}

#[test]
fn demo_exits_zero_and_tells_the_whole_story() {
    let out = nh().arg("demo").output().expect("run nh demo");
    assert!(out.status.success(), "demo must exit 0");
    let stdout = String::from_utf8_lossy(&out.stdout);
    // The two verdict levels are both on the page.
    assert!(stdout.contains("REAL"), "a REAL verdict is printed");
    assert!(
        stdout.contains("SUSPICIOUS"),
        "a SUSPICIOUS verdict is printed"
    );
    // The closing line, word for word.
    assert!(
        stdout.contains(
            "The naive strategy made more money. It was reading a number the \
             world didn't have yet. That is the whole problem this library \
             exists for."
        ),
        "the closing line ends the story"
    );
    // The table, the leak receipt, and the honest verdict in full.
    assert!(stdout.contains("sharpe") && stdout.contains("dsr_prob") && stdout.contains(" p "));
    assert!(stdout.contains("Leak receipt"));
    assert!(stdout.contains("decision days leaked:"));
    assert!(stdout.contains("max days early:"));
    assert!(stdout.contains("Honest verdict:"));
    assert!(stdout.contains("honest") && stdout.contains("naive"));
    // The naive one is printed HIGHER than the honest one - the inversion
    // is the story, so pin it in the output itself.  (Match the table rows,
    // not the Step 3 prose lines that also begin with the names.)
    let honest_line = stdout
        .lines()
        .find(|l| {
            let t = l.trim_start();
            t.starts_with("honest ") && t.contains("sharpe")
        })
        .expect("honest row");
    let naive_line = stdout
        .lines()
        .find(|l| {
            let t = l.trim_start();
            t.starts_with("naive ") && t.contains("sharpe")
        })
        .expect("naive row");
    let honest_sharpe: f64 = honest_line
        .split_whitespace()
        .find_map(|w| w.parse::<f64>().ok())
        .expect("honest sharpe in the row");
    let naive_sharpe: f64 = naive_line
        .split_whitespace()
        .find_map(|w| w.parse::<f64>().ok())
        .expect("naive sharpe in the row");
    assert!(
        naive_sharpe > honest_sharpe,
        "naive {naive_sharpe} must beat honest {honest_sharpe} on the page"
    );
    assert!(naive_line.contains("REAL") && honest_line.contains("SUSPICIOUS"));
}

#[test]
fn audit_clears_a_real_drift_and_exits_zero() {
    // mean 0.15%/day, sd 1%/day - Sharpe ~2.4, the honest-forensics pass.
    let path = returns_csv(
        "real",
        |i, rng| {
            if i % 2 == 0 {
                0.015 + 0.01 * rng.next_f64()
            } else {
                -0.005 + 0.01 * rng.next_f64()
            }
        },
        600,
        0xBEEF,
    );
    let out = nh().arg("audit").arg(&path).output().expect("run nh audit");
    assert!(out.status.success(), "a REAL verdict exits 0");
    let stdout = String::from_utf8_lossy(&out.stdout);
    assert!(stdout.contains("VERDICT: REAL"));
    assert!(stdout.contains("dsr_prob"));
    std::fs::remove_file(&path).ok();
}

#[test]
fn audit_flags_a_fantasy_and_exits_one() {
    // Zero-mean, zero-edge noise: p is huge, the verdict is entertainment.
    let path = returns_csv(
        "fantasy",
        |i, rng| {
            if i % 2 == 0 {
                0.01 + 0.002 * rng.next_f64()
            } else {
                -0.01 - 0.002 * rng.next_f64()
            }
        },
        600,
        0xC0FFEE,
    );
    let out = nh().arg("audit").arg(&path).output().expect("run nh audit");
    assert_eq!(out.status.code(), Some(1), "a FANTASY verdict exits 1");
    let stdout = String::from_utf8_lossy(&out.stdout);
    assert!(stdout.contains("VERDICT: FANTASY"));
    std::fs::remove_file(&path).ok();
}

#[test]
fn audit_respects_trials_and_refuses_bad_input() {
    // --trials raises the hurdle but a real drift stays REAL (exit 0).
    let path = returns_csv(
        "trials",
        |i, rng| {
            if i % 2 == 0 {
                0.02 + 0.01 * rng.next_f64()
            } else {
                -0.004 + 0.01 * rng.next_f64()
            }
        },
        600,
        7,
    );
    let out = nh()
        .args(["audit", path.to_str().expect("utf8 path"), "--trials", "40"])
        .output()
        .expect("run nh audit");
    assert!(out.status.success(), "drift survives 40-trial deflation");
    assert!(String::from_utf8_lossy(&out.stdout).contains("VERDICT:"));
    std::fs::remove_file(&path).ok();

    // Bad inputs: exit 2, with an error on stderr.
    let missing = nh()
        .args(["audit", "/nonexistent/returns.csv"])
        .output()
        .expect("run");
    assert_eq!(missing.status.code(), Some(2));
    assert!(!missing.stderr.is_empty());

    let garbage = temp_csv("garbage", "return\n0.01\noops\n");
    let out = nh().arg("audit").arg(&garbage).output().expect("run");
    assert_eq!(out.status.code(), Some(2));
    assert!(String::from_utf8_lossy(&out.stderr).contains("is not a number"));
    std::fs::remove_file(&garbage).ok();

    let one_value = temp_csv("short", "0.01\n");
    let out = nh().arg("audit").arg(&one_value).output().expect("run");
    assert_eq!(out.status.code(), Some(2));
    std::fs::remove_file(&one_value).ok();

    let headerless = temp_csv(
        "headerless",
        "0.02\n0.01\n0.03\n0.01\n0.02\n0.02\n0.03\n0.01\n",
    );
    let out = nh().arg("audit").arg(&headerless).output().expect("run");
    assert!(out.status.success(), "headerless one-column CSVs just work");
    std::fs::remove_file(&headerless).ok();
}

/// A weekday-spaced ISO stamp `week` weeks after 2024-01-02, at the close -
/// built with the engine's own calendar.
fn weekly_stamp(week: usize) -> String {
    use nh::core::Time;
    let anchor = Time::parse("2024-01-02").expect("anchor");
    anchor
        .plus_days((week as i64) * 7)
        .expect("in range")
        .to_date_iso()
        + "T20:00:00Z"
}

#[test]
fn check_passes_a_clean_tape_and_convicts_a_split() {
    // A noisy-but-honest year of one symbol, weekly rows: exit 0, no leaks.
    // (Rows carry real jitter so the scale-shock detector has a truthful
    // sigma to judge against - a perfectly geometric tape would flag its
    // own float rounding, which is the detector working, not a bug.)
    let mut rng = Xor32(0xA11CE);
    let mut clean = String::from("symbol,ts,open,high,low,close,volume\n");
    let mut close = 100.0;
    for week in 0..120 {
        let ts = weekly_stamp(week);
        let open = close;
        close *= 1.0 + (rng.next_f64() * 2.0 - 1.0) * 0.015;
        let high = open.max(close) * (1.0 + rng.next_f64() * 0.005);
        let low = open.min(close) * (1.0 - rng.next_f64() * 0.005);
        let volume = 800_000.0 + rng.next_f64() * 400_000.0;
        clean.push_str(&format!(
            "CLEAN,{ts},{open:.2},{high:.2},{low:.2},{close:.2},{volume:.0}\n"
        ));
    }
    let path = temp_csv("clean", &clean);
    let out = nh().arg("check").arg(&path).output().expect("run nh check");
    assert!(out.status.success(), "clean tape exits 0");
    let stdout = String::from_utf8_lossy(&out.stdout);
    assert!(stdout.contains("no findings") || stdout.contains("0 leaks"));
    assert!(stdout.contains("scanned 1 symbol(s): 0 leaks"));
    std::fs::remove_file(&path).ok();

    // The planted crime: a raw 4:1 overnight drop on 5x volume - exit 1.
    let mut rng = Xor32(0xA11CE); // same weather, so only the crime differs
    let mut split = String::from("symbol,ts,open,high,low,close,volume\n");
    let mut close = 100.0;
    let mut volume = 1_000_000.0;
    for week in 0..120 {
        let ts = weekly_stamp(week);
        let open = close;
        if week == 60 {
            // The crime, planted exactly: 4:1 on 5x volume, unadjusted.
            // The open prints post-split, so the geometry stays honest and
            // the split detector is the only thing that fires.
            close /= 4.0;
            volume *= 5.0;
            let open = close;
            split.push_str(&format!(
                "SPLITTY,{ts},{open:.4},{:.4},{:.4},{close:.4},{volume:.0}\n",
                close * 1.01,
                close * 0.99
            ));
        } else {
            close *= 1.0 + (rng.next_f64() * 2.0 - 1.0) * 0.015;
            let high = open.max(close) * (1.0 + rng.next_f64() * 0.005);
            let low = open.min(close) * (1.0 - rng.next_f64() * 0.005);
            volume = 800_000.0 + rng.next_f64() * 400_000.0;
            split.push_str(&format!(
                "SPLITTY,{ts},{open:.4},{high:.4},{low:.4},{close:.4},{volume:.0}\n"
            ));
        }
    }
    let path = temp_csv("split", &split);
    let out = nh().arg("check").arg(&path).output().expect("run nh check");
    assert_eq!(out.status.code(), Some(1), "a leak exits 1");
    let stdout = String::from_utf8_lossy(&out.stdout);
    assert!(stdout.contains("SPLIT_SUSPECTED"));
    assert!(stdout.contains("scanned 1 symbol(s): 1 leaks"));
    std::fs::remove_file(&path).ok();
}

#[test]
fn check_flags_a_knowledge_order_lie() {
    // knowledge_time BEFORE event_time: impossible, and the police say so.
    let body = "symbol,ts,knowledge,open,high,low,close,volume\n\
                LIAR,2024-03-01T20:00:00Z,2024-02-28T20:00:00Z,10,11,9,10,1000\n";
    let path = temp_csv("liar", body);
    let out = nh().arg("check").arg(&path).output().expect("run nh check");
    assert_eq!(out.status.code(), Some(1));
    let stdout = String::from_utf8_lossy(&out.stdout);
    assert!(stdout.contains("KNOWLEDGE_BEFORE_EVENT"));
    assert!(stdout.contains("LIAR"));
    std::fs::remove_file(&path).ok();
}

#[test]
fn unknown_commands_and_missing_files_exit_two() {
    let out = nh().arg("frobnicate").output().expect("run");
    assert_eq!(out.status.code(), Some(2));
    let out = nh().output().expect("run");
    assert_eq!(out.status.code(), Some(2));
    let out = nh()
        .args(["check", "/nonexistent/bars.csv"])
        .output()
        .expect("run");
    assert_eq!(out.status.code(), Some(2));
    // audit with no csv: usage-level failure.
    let out = nh().args(["audit"]).output().expect("run");
    assert_eq!(out.status.code(), Some(2));
}
