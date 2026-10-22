//! The two-clock guard, pinned in Rust: the guard fires on future reads,
//! revisions resolve by clock, rewinds are refused, and the parser keeps
//! its promises.

use nh::core::{AsOfKeyError, Clock, EntryError, Ledger, ReadError, Time, Value};
use std::str::FromStr;

fn t(s: &str) -> Time {
    Time::parse(s).expect("valid time")
}

fn fund_ledger() -> Ledger {
    // The README's restatement, in engine form: original filed, then
    // re-filed 30% lower three weeks later.
    let mut ledger = Ledger::new("fundamentals").expect("named ledger");
    ledger
        .append(
            "SYM2.revenue",
            Value::Num(253.4),
            t("2023-12-31"),
            t("2024-02-09"),
        )
        .expect("original");
    ledger
        .append(
            "SYM2.revenue",
            Value::Num(177.4),
            t("2023-12-31"),
            t("2024-03-01"),
        )
        .expect("restatement");
    ledger
}

#[test]
fn guard_fires_on_future_reads() {
    let ledger = fund_ledger();
    let clock = Clock::at(t("2024-02-15"));
    let view = ledger.view(&clock);
    // The original is knowable; the restatement is not on the table yet.
    assert_eq!(
        view.get("SYM2.revenue").expect("knowable"),
        &Value::Num(253.4)
    );
    // Rewind the world's attention one week and read a key whose FIRST
    // revision is in the future: the guard fires with the full story.
    let clock = Clock::at(t("2024-01-15"));
    let view = ledger.view(&clock);
    match view.get("SYM2.revenue") {
        Err(ReadError::Leak {
            key,
            knowledge_time,
            as_of,
        }) => {
            assert_eq!(key, "SYM2.revenue");
            assert_eq!(knowledge_time, t("2024-02-09"));
            assert_eq!(as_of, t("2024-01-15"));
            let text = ReadError::Leak {
                key,
                knowledge_time,
                as_of,
            }
            .to_string();
            assert!(text.contains("LEAK"), "message names the crime: {text}");
            assert!(text.contains("25 days"), "message quantifies it: {text}");
        }
        other => panic!("expected a Leak, got {other:?}"),
    }
}

#[test]
fn revisions_resolve_by_the_clock() {
    let ledger = fund_ledger();
    // Before the original: the number does not exist — not None, not zero.
    let clock = Clock::at(t("2024-01-02"));
    let view = ledger.view(&clock);
    assert_eq!(view.revisions("SYM2.revenue").len(), 0);
    assert!(!view.contains("SYM2.revenue"));
    // On the original's knowledge day: the original.
    let clock = Clock::at(t("2024-02-09"));
    let view = ledger.view(&clock);
    assert_eq!(view.get("SYM2.revenue").expect("ok"), &Value::Num(253.4));
    assert_eq!(view.revisions("SYM2.revenue").len(), 1);
    // Between filings, the view still shows the original.
    let clock = Clock::at(t("2024-02-28"));
    let view = ledger.view(&clock);
    assert_eq!(view.get("SYM2.revenue").expect("ok"), &Value::Num(253.4));
    // From the restatement's knowledge day: the restatement — and both
    // revisions are "known" (a restatement in progress, resolved).
    let clock = Clock::at(t("2024-03-01"));
    let view = ledger.view(&clock);
    assert_eq!(view.get("SYM2.revenue").expect("ok"), &Value::Num(177.4));
    assert_eq!(view.revisions("SYM2.revenue").len(), 2);
}

#[test]
fn rewind_is_refused_and_the_clock_does_not_move() {
    let clock = Clock::at(t("2024-03-01"));
    let rewound = clock.advance_to(t("2024-02-15"));
    assert!(rewound.is_err());
    assert_eq!(clock.now(), t("2024-03-01"), "a refused advance is a no-op");
    let negative = clock.advance(-1);
    assert!(negative.is_err());
    // Forward is fine, and equal-now advances are allowed (not rewinds).
    assert_eq!(clock.advance(0).expect("ok"), t("2024-03-01"));
    assert_eq!(
        clock.advance_to(t("2024-03-01")).expect("ok"),
        t("2024-03-01")
    );
    let err = clock.advance_to(t("2024-02-15")).expect_err("rewind");
    assert!(
        err.to_string().contains("cannot rewind"),
        "the message says what happened: {err}"
    );
}

#[test]
fn impossible_entries_are_refused() {
    let mut ledger = Ledger::new("f").expect("ok");
    // knowledge before event: the world can't know things before they happen.
    let err = ledger
        .append("X", Value::Num(1.0), t("2024-03-10"), t("2024-03-01"))
        .expect_err("impossible");
    assert!(matches!(
        err,
        nh::core::LedgerError::BadEntry(EntryError::ImpossibleOrder { .. })
    ));
    assert!(err
        .to_string()
        .contains("can't know things before they happen"));
    // Empty keys and empty names are refused too.
    assert!(Ledger::new("").is_err());
    let err = ledger
        .append("", Value::Num(1.0), t("2024-03-01"), t("2024-03-01"))
        .expect_err("empty key");
    assert!(err.to_string().contains("key must be non-empty"));
}

#[test]
fn unknown_keys_are_not_yet_known_not_leaks() {
    let ledger = fund_ledger();
    let clock = Clock::at(t("2024-06-01"));
    let view = ledger.view(&clock);
    match view.get("SYM9.revenue") {
        Err(ReadError::NotYetKnown { key }) => assert_eq!(key, "SYM9.revenue"),
        other => panic!("expected NotYetKnown, got {other:?}"),
    }
    assert!(!view.contains("SYM9.revenue"));
    // A key that exists but only in the future is a LEAK, not a NotYetKnown —
    // the distinction the guard exists to make.
    let clock = Clock::at(t("2024-01-02"));
    let early = ledger.view(&clock);
    assert!(matches!(
        early.get("SYM2.revenue"),
        Err(ReadError::Leak { .. })
    ));
    // get_num_or swallows both, loudly documented as the quiet path.
    assert_eq!(early.get_num_or("SYM2.revenue", -1.0), -1.0);
}

#[test]
fn known_keys_only_lists_knowable_keys() {
    let mut ledger = Ledger::new("f").expect("ok");
    ledger
        .append("A.old", Value::Num(1.0), t("2023-01-01"), t("2023-01-15"))
        .expect("ok");
    ledger
        .append("B.late", Value::Num(2.0), t("2023-06-01"), t("2024-02-01"))
        .expect("ok");
    let clock = Clock::at(t("2023-03-01"));
    let view = ledger.view(&clock);
    assert_eq!(view.known_keys(), vec!["A.old".to_string()]);
    let clock = Clock::at(t("2024-03-01"));
    let view = ledger.view(&clock);
    assert_eq!(view.known_keys().len(), 2);
    // The raw ledger exposes both keys all along — iterating IT is the
    // survivorship-bias trap the docs warn about.
    assert_eq!(ledger.keys().len(), 2);
}

#[test]
fn latest_is_the_terminal_snapshot_value_at_respects_a_clock() {
    let ledger = fund_ledger();
    // The terminal snapshot is the restatement — the naive read.
    assert_eq!(
        ledger.latest("SYM2.revenue").expect("exists").value,
        Value::Num(177.4)
    );
    // value_at with a clock is the honest read.
    assert!(matches!(
        ledger.value_at("SYM2.revenue", t("2024-02-15")),
        Ok(Value::Num(253.4))
    ));
    // value_at with no revision knowable yet: the rich AsOfKeyError.
    let err = ledger
        .value_at("SYM3.revenue", t("2024-01-01"))
        .expect_err("nothing knowable");
    let AsOfKeyError {
        key,
        n_revisions,
        first_knowledge,
        ..
    } = &err;
    assert_eq!(key, "SYM3.revenue");
    assert_eq!(*n_revisions, 0);
    assert_eq!(*first_knowledge, None);
    assert!(err.to_string().contains("no revision knowable"));
}

#[test]
fn entries_after_exposes_the_raw_future() {
    let ledger = fund_ledger();
    let now = t("2024-02-15");
    let future = ledger.entries_after(now);
    // Exactly the restatement is unknowable at Feb 15.
    assert_eq!(future.len(), 1);
    assert_eq!(future[0].revision, 1);
    assert_eq!(ledger.len(), 2);
    assert!(!ledger.is_empty());
    assert_eq!(ledger.name(), "fundamentals");
}

#[test]
fn iso_parser_keeps_its_documented_promises() {
    // Roundtrip through the only spelling the Display emits.
    for text in [
        "2024-03-01",
        "2024-03-01T14:00:00Z",
        "2024-03-01T14:00:00+00:00",
        "2024-03-01 09:30:00.123456",
    ] {
        let parsed = Time::from_str(text).expect("accepted");
        let again = Time::from_str(&parsed.to_iso()).expect("roundtrip");
        assert_eq!(parsed, again, "roundtrip of {text}");
    }
    // Fractional truncation is documented, deterministic.
    let frac = Time::parse("2024-03-01T14:00:00.1234567891").expect("truncates past nanos");
    assert_eq!(frac.to_iso(), "2024-03-01T14:00:00.123456+00:00");
    // Ordering is instant ordering.
    assert!(t("2024-03-01T14:00:00Z") < t("2024-03-01T14:00:01Z"));
}
