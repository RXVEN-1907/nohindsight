//! nohindsight core - the two clocks and the guard.
//!
//! Every datum in nohindsight carries two times:
//!
//! * `event_time` - when the thing happened in the world
//! * `knowledge_time` - when an honest observer could have first known it
//!
//! A backtest that reads data whose `knowledge_time` is after "now" has
//! leaked: it used information the future hadn't produced yet.  The guard
//! makes that a hard error, not a warning.  Data access goes through an
//! [`AsOfView`] attached to a [`Clock`]; the view physically refuses to show
//! anything the clock hasn't seen happen.  There is no bypass method on the
//! view - that's the point.
//!
//! Time is [`Time`]: i64 nanoseconds since the Unix epoch, UTC only.  No
//! chrono, no timezone database.  The ISO-8601 parser accepts the one
//! spelling finance data actually uses ("YYYY-MM-DD", optionally
//! "THH:MM[:SS[.fff]]", optionally "Z" or "+00:00"; a naive timestamp is
//! assumed UTC - naive timestamps in finance data are ambiguous enough
//! already, so we pick UTC and say so rather than guess a market timezone).
//!
//! Zero dependencies.  Everything in the engine is built on this file.

use std::cell::Cell;
use std::collections::BTreeMap;
use std::error::Error;
use std::fmt;
use std::str::FromStr;

pub const NANOS_PER_SEC: i64 = 1_000_000_000;
const NANOS_PER_DAY: i64 = 86_400 * NANOS_PER_SEC;

// ---------------------------------------------------------------------------
// civil-calendar helpers (pure integer math, Howard Hinnant's algorithms)
// ---------------------------------------------------------------------------

/// Days since 1970-01-01 for a civil date.  Proleptic Gregorian, no year 0
/// confusion to worry about in finance ranges (year must be >= 1 to be
/// meaningful; the nanosecond `i64` really bounds us to 1678..2262).
pub fn days_from_civil(y: i64, m: u32, d: u32) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400; // [0, 399]
    let mp = (i64::from(m) + 9) % 12; // March = 0
    let doy = (153 * mp + 2) / 5 + i64::from(d) - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

/// The civil (year, month, day) for a count of days since 1970-01-01.
pub fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097; // [0, 146096]
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    (y + i64::from(m <= 2), m as u32, d)
}

/// Gregorian leap-year rule.
pub fn is_leap_year(y: i64) -> bool {
    (y % 4 == 0 && y % 100 != 0) || y % 400 == 0
}

/// Days in a month, leap years included.
pub fn days_in_month(y: i64, m: u32) -> u32 {
    match m {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 if is_leap_year(y) => 29,
        2 => 28,
        _ => 0,
    }
}

// ---------------------------------------------------------------------------
// Time
// ---------------------------------------------------------------------------

/// An instant: i64 nanoseconds since the epoch, UTC.
///
/// Copyable, totally ordered, and nothing else - deliberately dumber than a
/// timezone-aware datetime so the guard's arithmetic can't hide a surprise.
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Debug, Hash)]
pub struct Time {
    nanos: i64,
}

/// What the ISO-8601 parser refuses, and why.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum TimeError {
    /// Text is not the accepted spelling.
    BadFormat { text: String, why: String },
    /// A zone offset other than UTC was supplied.
    NotUtc { zone: String },
    /// The instant does not fit in i64 nanos since the epoch.
    OutOfRange { text: String },
}

impl fmt::Display for TimeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            TimeError::BadFormat { text, why } => {
                write!(f, "cannot interpret {text:?} as a UTC time: {why}")
            }
            TimeError::NotUtc { zone } => {
                write!(f, "only UTC is supported (Z or +00:00), got zone {zone:?}")
            }
            TimeError::OutOfRange { text } => {
                write!(f, "time {text:?} is outside the representable range")
            }
        }
    }
}

impl Error for TimeError {}

fn digits(text: &str, what: &str) -> Result<u32, TimeError> {
    if text.is_empty() || !text.bytes().all(|b| b.is_ascii_digit()) {
        return Err(TimeError::BadFormat {
            text: text.to_string(),
            why: format!("{what} must be digits"),
        });
    }
    text.parse::<u32>().map_err(|_| TimeError::BadFormat {
        text: text.to_string(),
        why: format!("{what} does not fit in u32"),
    })
}

impl Time {
    /// Build from raw nanoseconds since the epoch.
    pub fn from_nanos(nanos: i64) -> Time {
        Time { nanos }
    }

    /// Build from whole seconds since the epoch.
    pub fn from_secs(secs: i64) -> Time {
        Time {
            nanos: secs * NANOS_PER_SEC,
        }
    }

    /// Raw nanoseconds since the epoch.
    pub fn as_nanos(self) -> i64 {
        self.nanos
    }

    /// Whole days since the epoch (floor division - times inside a day
    /// belong to the day they started in).
    pub fn date_days(self) -> i64 {
        self.nanos.div_euclid(NANOS_PER_DAY)
    }

    /// Midnight UTC of the day `z` days since the epoch.
    pub fn from_date_days(z: i64) -> Time {
        Time {
            nanos: z * NANOS_PER_DAY,
        }
    }

    /// Parse the accepted ISO-8601 spellings (see the module docs).
    pub fn parse(text: &str) -> Result<Time, TimeError> {
        let s = text.trim();
        let bytes = s.as_bytes();
        if bytes.len() < 10
            || bytes[4] != b'-'
            || bytes[7] != b'-'
            || !bytes[0..4].iter().all(|b| b.is_ascii_digit())
            || !bytes[5..7].iter().all(|b| b.is_ascii_digit())
            || !bytes[8..10].iter().all(|b| b.is_ascii_digit())
        {
            return Err(TimeError::BadFormat {
                text: s.to_string(),
                why: "expected YYYY-MM-DD".to_string(),
            });
        }
        let year = s[0..4].parse::<i64>().map_err(|_| TimeError::BadFormat {
            text: s.to_string(),
            why: "year does not fit".to_string(),
        })?;
        let month = digits(&s[5..7], "month")?;
        let day = digits(&s[8..10], "day")?;
        if !(1..=12).contains(&month) {
            return Err(TimeError::BadFormat {
                text: s.to_string(),
                why: format!("month {month} out of range 1..=12"),
            });
        }
        let day_max = days_in_month(year, month);
        if day < 1 || day > day_max {
            return Err(TimeError::BadFormat {
                text: s.to_string(),
                why: format!("day {day} out of range 1..={day_max}"),
            });
        }

        let mut hour = 0u32;
        let mut minute = 0u32;
        let mut second = 0u32;
        let mut subnanos = 0i64;

        let rest = &s[10..];
        if !rest.is_empty() {
            let sep = rest.as_bytes()[0];
            if sep != b'T' && sep != b't' && sep != b' ' {
                return Err(TimeError::BadFormat {
                    text: s.to_string(),
                    why: "date and time must be separated by 'T' (or a space)".to_string(),
                });
            }
            let t = &rest[1..];
            // HH:MM required, :SS optional, .fff optional.
            let tbytes = t.as_bytes();
            let bad = |why: &str| {
                Err(TimeError::BadFormat {
                    text: s.to_string(),
                    why: why.to_string(),
                })
            };
            if tbytes.len() < 5 || tbytes[2] != b':' {
                return bad("time must be HH:MM or HH:MM:SS");
            }
            hour = digits(&t[0..2], "hour")?;
            minute = digits(&t[3..5], "minute")?;
            let mut idx = 5usize;
            if tbytes.len() > idx && tbytes[idx] == b':' {
                if tbytes.len() < idx + 3 {
                    return bad("seconds must be two digits");
                }
                second = digits(&t[idx + 1..idx + 3], "second")?;
                idx += 3;
            }
            if second > 59 {
                return bad("leap seconds are not representable; use second 58 or 59");
            }
            if tbytes.len() > idx && (tbytes[idx] == b'.' || tbytes[idx] == b',') {
                let frac_start = idx + 1;
                let mut frac_end = frac_start;
                while frac_end < tbytes.len() && tbytes[frac_end].is_ascii_digit() {
                    frac_end += 1;
                }
                if frac_end == frac_start {
                    return bad("fractional seconds need at least one digit");
                }
                // Truncate past nanosecond precision, documented.
                let frac = &t[frac_start..frac_end.min(frac_start + 9)];
                let padded = format!("{frac:0<9}");
                subnanos = padded.parse::<i64>().unwrap_or(0);
                idx = frac_end;
            }
            // Whatever is left must be a zone.
            let zone = &t[idx..];
            if !zone.is_empty() {
                if zone == "Z" {
                    // UTC, nothing to do.
                } else if zone == "+00:00" || zone == "-00:00" {
                    // UTC under its other name.
                } else if zone.starts_with('+') || zone.starts_with('-') {
                    return Err(TimeError::NotUtc {
                        zone: zone.to_string(),
                    });
                } else {
                    return bad("trailing characters after the time");
                }
            }
            if hour > 23 {
                return bad("hour out of range 0..=23");
            }
            if minute > 59 {
                return bad("minute out of range 0..=59");
            }
        }

        let z = days_from_civil(year, month, day);
        let nanos = z
            .checked_mul(NANOS_PER_DAY)
            .and_then(|d| {
                d.checked_add(i64::from(hour) * 3_600 * NANOS_PER_SEC)
                    .and_then(|h| {
                        h.checked_add(i64::from(minute) * 60 * NANOS_PER_SEC)
                            .and_then(|m| {
                                m.checked_add(i64::from(second) * NANOS_PER_SEC + subnanos)
                            })
                    })
            })
            .ok_or_else(|| TimeError::OutOfRange {
                text: s.to_string(),
            })?;
        Ok(Time { nanos })
    }

    /// (year, month, day) of this instant's UTC date.
    pub fn date_parts(self) -> (i64, u32, u32) {
        civil_from_days(self.date_days())
    }

    /// Day of week: 0 = Monday .. 6 = Sunday.  1970-01-01 was a Thursday.
    pub fn weekday(self) -> u32 {
        (self.date_days().rem_euclid(7) as u32 + 3) % 7
    }

    /// The same UTC date, with the time of day set to h:m:s.
    pub fn with_hms(self, h: u32, m: u32, s: u32) -> Result<Time, TimeError> {
        if h > 23 || m > 59 || s > 59 {
            return Err(TimeError::BadFormat {
                text: format!("{h:02}:{m:02}:{s:02}"),
                why: "time-of-day out of range".to_string(),
            });
        }
        Time::from_date_days(self.date_days()).plus_nanos(
            i64::from(h) * 3_600 * NANOS_PER_SEC
                + i64::from(m) * 60 * NANOS_PER_SEC
                + i64::from(s) * NANOS_PER_SEC,
        )
    }

    /// Move by whole calendar days (keeping the time of day).
    pub fn plus_days(self, days: i64) -> Result<Time, TimeError> {
        self.plus_nanos(
            days.checked_mul(NANOS_PER_DAY)
                .ok_or_else(|| TimeError::OutOfRange {
                    text: format!("{days} days"),
                })?,
        )
    }

    /// Move by a fractional number of days (a 38.5-day filing lag), truncated
    /// to nanoseconds.  Assumption stated: fractional-day lags are a
    /// modelling convenience; the truncation is deterministic and at most one
    /// nanosecond.
    pub fn plus_days_f64(self, days: f64) -> Result<Time, TimeError> {
        let delta = (days * NANOS_PER_DAY as f64) as i64;
        self.plus_nanos(delta)
    }

    /// Move by whole nanoseconds, refusing overflow.
    pub fn plus_nanos(self, delta: i64) -> Result<Time, TimeError> {
        self.nanos
            .checked_add(delta)
            .map(Time::from_nanos)
            .ok_or_else(|| TimeError::OutOfRange {
                text: self.to_iso(),
            })
    }

    /// Difference `self - other` in nanoseconds (may be negative).
    pub fn diff_nanos(self, other: Time) -> i64 {
        self.nanos - other.nanos
    }

    /// ISO-8601, Python-datetime style: "2024-03-01T14:00:00+00:00", with a
    /// six-digit fraction only when there is one.
    pub fn to_iso(self) -> String {
        let (y, mo, d) = self.date_parts();
        let rem = self.nanos.rem_euclid(NANOS_PER_DAY);
        let secs = rem / NANOS_PER_SEC;
        let frac = rem % NANOS_PER_SEC;
        let (h, mi, s) = (secs / 3600, (secs % 3600) / 60, secs % 60);
        if frac == 0 {
            format!("{y:04}-{mo:02}-{d:02}T{h:02}:{mi:02}:{s:02}+00:00")
        } else {
            format!(
                "{y:04}-{mo:02}-{d:02}T{h:02}:{mi:02}:{s:02}.{:.6}+00:00",
                frac / 1_000
            )
        }
    }

    /// "2024-03-01" - just the date.
    pub fn to_date_iso(self) -> String {
        let (y, mo, d) = self.date_parts();
        format!("{y:04}-{mo:02}-{d:02}")
    }
}

impl fmt::Display for Time {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.to_iso())
    }
}

impl FromStr for Time {
    type Err = TimeError;
    fn from_str(s: &str) -> Result<Time, TimeError> {
        Time::parse(s)
    }
}

/// "21 days, 0:00:00" - a Python-timedelta-shaped duration string for the
/// leak messages.  Assumes a non-negative delta (the guard only ever
/// measures forward distances).
fn format_delta(nanos: i64) -> String {
    let days = nanos / NANOS_PER_DAY;
    let rem = nanos % NANOS_PER_DAY;
    let secs = rem / NANOS_PER_SEC;
    let frac = rem % NANOS_PER_SEC;
    let (h, mi, s) = (secs / 3600, (secs % 3600) / 60, secs % 60);
    let day_part = match days {
        0 => String::new(),
        1 => "1 day, ".to_string(),
        d => format!("{d} days, "),
    };
    if frac == 0 {
        format!("{day_part}{h:02}:{mi:02}:{s:02}")
    } else {
        format!("{day_part}{h:02}:{mi:02}:{s:02}.{:.6}", frac / 1_000)
    }
}

// ---------------------------------------------------------------------------
// Value / Bar
// ---------------------------------------------------------------------------

/// One OHLCV bar - the shape a price series takes inside a [`Value`].
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Bar {
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
}

/// What a ledger entry carries.  Numbers cover the fundamentals, macro
/// vintages and anything else scalar; bars cover OHLCV price data; text is
/// there because sometimes the datum is a word.
#[derive(Clone, Debug, PartialEq)]
pub enum Value {
    Num(f64),
    Text(String),
    Bar(Bar),
}

impl Value {
    /// The number, if this is one.
    pub fn as_num(&self) -> Option<f64> {
        match self {
            Value::Num(x) => Some(*x),
            _ => None,
        }
    }

    /// The bar, if this is one.
    pub fn as_bar(&self) -> Option<Bar> {
        match self {
            Value::Bar(b) => Some(*b),
            _ => None,
        }
    }
}

// ---------------------------------------------------------------------------
// Entry
// ---------------------------------------------------------------------------

/// One revision of one datum.  Immutable once built.
#[derive(Clone, Debug, PartialEq)]
pub struct Entry {
    pub key: String,
    pub value: Value,
    pub event_time: Time,
    pub knowledge_time: Time,
    pub revision: u64,
}

/// Entry-level validation failures.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum EntryError {
    /// Empty keys carry no story.
    EmptyKey,
    /// knowledge_time before event_time: the world can't know things before
    /// they happen.
    ImpossibleOrder {
        key: String,
        event_time: Time,
        knowledge_time: Time,
    },
}

impl fmt::Display for EntryError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            EntryError::EmptyKey => write!(f, "key must be non-empty"),
            EntryError::ImpossibleOrder {
                key,
                event_time,
                knowledge_time,
            } => write!(
                f,
                "impossible entry for {key:?}: knowledge_time ({}) precedes \
                 event_time ({}) - the world can't know things before they happen",
                knowledge_time, event_time
            ),
        }
    }
}

impl Error for EntryError {}

impl Entry {
    /// Validate and build: non-empty key, knowledge_time >= event_time.
    pub fn new(
        key: &str,
        value: Value,
        event_time: Time,
        knowledge_time: Time,
        revision: u64,
    ) -> Result<Entry, EntryError> {
        if key.is_empty() {
            return Err(EntryError::EmptyKey);
        }
        if knowledge_time < event_time {
            return Err(EntryError::ImpossibleOrder {
                key: key.to_string(),
                event_time,
                knowledge_time,
            });
        }
        Ok(Entry {
            key: key.to_string(),
            value,
            event_time,
            knowledge_time,
            revision,
        })
    }

    /// Is this revision knowable at `when`?
    pub fn at(&self, when: Time) -> bool {
        self.knowledge_time <= when
    }
}

// ---------------------------------------------------------------------------
// Clock
// ---------------------------------------------------------------------------

/// A clock you can freeze, fork, and walk.  Backtests use this instead of
/// the wall clock; the present is whatever the clock says it is.
///
/// Time only moves forward.  Trying to rewind is an error - a backtest that
/// rewinds its clock is a time machine, not a backtest.
///
/// The "now" lives in a `Cell` on purpose: the clock is shared mutable state
/// by design.  A replay engine and the views bound to the same clock must
/// see the same instant, and Python's clock gives exactly that aliasing;
/// `Cell<i64>` gives it to us without `unsafe` or `RefCell` ceremony.
/// Advancing takes `&self`, so one clock can be walked while views borrow it.
#[derive(Clone, Debug)]
pub struct Clock {
    now: Cell<i64>,
}

/// Clock-level failures: only ever "you tried to go backwards".
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ClockError {
    /// A rewind attempt, with both instants named.
    Rewind { from: Time, to: Time },
    /// A negative advance delta.
    NegativeDelta { delta_nanos: i64 },
}

impl fmt::Display for ClockError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ClockError::Rewind { from, to } => {
                write!(f, "clock cannot rewind from {from} to {to}")
            }
            ClockError::NegativeDelta { delta_nanos } => {
                write!(f, "clock cannot rewind (delta {delta_nanos} ns < 0)")
            }
        }
    }
}

impl Error for ClockError {}

impl Clock {
    /// A clock frozen at `start`.
    pub fn at(start: Time) -> Clock {
        Clock {
            now: Cell::new(start.as_nanos()),
        }
    }

    /// The present, per this clock.
    pub fn now(&self) -> Time {
        Time::from_nanos(self.now.get())
    }

    /// Move to `when`; refused if that is backwards.  Returns the new now.
    pub fn advance_to(&self, when: Time) -> Result<Time, ClockError> {
        let from = self.now.get();
        if when.as_nanos() < from {
            return Err(ClockError::Rewind {
                from: Time::from_nanos(from),
                to: when,
            });
        }
        self.now.set(when.as_nanos());
        Ok(when)
    }

    /// Move forward by `delta_nanos`; refused if negative.
    pub fn advance(&self, delta_nanos: i64) -> Result<Time, ClockError> {
        if delta_nanos < 0 {
            return Err(ClockError::NegativeDelta { delta_nanos });
        }
        let target = Time::from_nanos(self.now.get())
            .plus_nanos(delta_nanos)
            .map_err(|_| ClockError::NegativeDelta { delta_nanos })?;
        self.advance_to(target)
    }

    /// An independent clock starting at the same instant.
    pub fn fork(&self) -> Clock {
        Clock {
            now: Cell::new(self.now.get()),
        }
    }
}

// ---------------------------------------------------------------------------
// Ledger
// ---------------------------------------------------------------------------

/// Ledger-level failures.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum LedgerError {
    /// The ledger needs a name.
    EmptyName,
    /// The entry being appended was invalid.
    BadEntry(EntryError),
    /// A time helper refused its arithmetic while building the entry.
    BadTime(TimeError),
}

impl fmt::Display for LedgerError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            LedgerError::EmptyName => write!(f, "ledger needs a name"),
            LedgerError::BadEntry(e) => write!(f, "{e}"),
            LedgerError::BadTime(e) => write!(f, "{e}"),
        }
    }
}

impl Error for LedgerError {
    fn source(&self) -> Option<&(dyn Error + 'static)> {
        match self {
            LedgerError::BadEntry(e) => Some(e),
            LedgerError::BadTime(e) => Some(e),
            _ => None,
        }
    }
}

impl From<TimeError> for LedgerError {
    fn from(e: TimeError) -> Self {
        LedgerError::BadTime(e)
    }
}

/// The honest "not found": no revision of `key` was knowable yet.
///
/// Before the first filing, the fundamental does not exist - it is not None,
/// not zero, not forward-filled.  This error is the data saying so.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AsOfKeyError {
    pub key: String,
    pub as_of: Time,
    pub n_revisions: usize,
    pub first_knowledge: Option<Time>,
}

impl fmt::Display for AsOfKeyError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let first = match self.first_knowledge {
            Some(t) => t.to_iso(),
            None => "\u{2014}".to_string(),
        };
        write!(
            f,
            "{:?}: no revision knowable at {} ({} revisions exist, first at {})",
            self.key, self.as_of, self.n_revisions, first
        )
    }
}

impl Error for AsOfKeyError {}

/// The guard's refusal, with the full story attached so a report layer can
/// print exactly which datum leaked, by how much, and through which key.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ReadError {
    /// The key exists, but its first revision is in the clock's future.
    Leak {
        key: String,
        knowledge_time: Time,
        as_of: Time,
    },
    /// The key was never appended to this ledger at all.
    NotYetKnown { key: String },
}

impl fmt::Display for ReadError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            ReadError::Leak {
                key,
                knowledge_time,
                as_of,
            } => write!(
                f,
                "LEAK via {key:?}: first knowable at {} but the clock says {} \
                 ({}) into the future.",
                knowledge_time,
                as_of,
                format_delta(knowledge_time.diff_nanos(*as_of))
            ),
            ReadError::NotYetKnown { key } => {
                write!(f, "{key:?} does not exist in this ledger")
            }
        }
    }
}

impl Error for ReadError {}

/// An append-only, keyed, revisioned series.
///
/// Appending a key again creates the next revision; nothing is ever
/// overwritten or deleted.  Revisions are numbered by append order per key;
/// each key's revision list is kept sorted by (knowledge_time, revision), so
/// "what was knowable at T" is a walk from the back of that list.
///
/// Storage note, stated: Python kept the key map in insertion order; this
/// implementation keeps it in a `BTreeMap`, i.e. sorted by key.  Nothing in
/// the honest path depends on the difference - `known_keys` is the thing
/// strategies iterate, and it filters by the clock either way.
pub struct Ledger {
    name: String,
    entries: Vec<Entry>,
    by_key: BTreeMap<String, Vec<Entry>>,
}

impl Ledger {
    /// A named, empty ledger.
    pub fn new(name: &str) -> Result<Ledger, LedgerError> {
        if name.is_empty() {
            return Err(LedgerError::EmptyName);
        }
        Ok(Ledger {
            name: name.to_string(),
            entries: Vec::new(),
            by_key: BTreeMap::new(),
        })
    }

    /// The ledger's name.
    pub fn name(&self) -> &str {
        &self.name
    }

    /// Append the next revision of `key`.  Validates like [`Entry::new`].
    pub fn append(
        &mut self,
        key: &str,
        value: Value,
        event_time: Time,
        knowledge_time: Time,
    ) -> Result<Entry, LedgerError> {
        let revision = self.by_key.get(key).map_or(0u64, |v| v.len() as u64);
        let entry = Entry::new(key, value, event_time, knowledge_time, revision)
            .map_err(LedgerError::BadEntry)?;
        let prior = self.by_key.entry(key.to_string()).or_default();
        // Keep the per-key list sorted by (knowledge_time, revision).
        let idx =
            prior.partition_point(|e| (e.knowledge_time, e.revision) <= (knowledge_time, revision));
        prior.insert(idx, entry.clone());
        self.entries.push(entry.clone());
        Ok(entry)
    }

    /// Every revision ever appended for `key`, in knowledge order.
    /// This is the raw, unguarded side of the ledger - strategies should
    /// not be holding it; the checks and the leak accountant do.
    pub fn history(&self, key: &str) -> &[Entry] {
        self.by_key.get(key).map_or(&[], |v| v.as_slice())
    }

    /// All keys with at least one revision (sorted; see the storage note).
    pub fn keys(&self) -> Vec<String> {
        self.by_key.keys().cloned().collect()
    }

    /// Every entry in append order.
    pub fn entries(&self) -> &[Entry] {
        &self.entries
    }

    /// Raw access to everything NOT knowable at `when`.
    ///
    /// This is the unsafe side of the ledger.  The leak accountant uses it
    /// to measure how much future there was to leak; strategies never touch
    /// it.
    pub fn entries_after(&self, when: Time) -> Vec<&Entry> {
        self.entries
            .iter()
            .filter(|e| e.knowledge_time > when)
            .collect()
    }

    /// Number of revisions appended, across all keys.
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    /// True when nothing has been appended yet.
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    /// Latest revision of `key` knowable at `as_of` - the only honest
    /// question a raw ledger can answer.  [`AsOfKeyError`] when nothing was
    /// knowable yet: the truthful answer, not a filled value.
    pub fn value_at(&self, key: &str, as_of: Time) -> Result<&Value, AsOfKeyError> {
        let prior = self.history(key);
        for entry in prior.iter().rev() {
            if entry.at(as_of) {
                return Ok(&entry.value);
            }
        }
        Err(AsOfKeyError {
            key: key.to_string(),
            as_of,
            n_revisions: prior.len(),
            first_knowledge: prior.first().map(|e| e.knowledge_time),
        })
    }

    /// Revisions of `key` knowable at `as_of`, oldest first.  Two revisions
    /// known at once means: the number changed and the world had noticed by
    /// then - a restatement in progress.
    pub fn revisions_known(&self, key: &str, as_of: Time) -> Vec<&Entry> {
        self.history(key).iter().filter(|e| e.at(as_of)).collect()
    }

    /// The terminal snapshot: the revision with the highest knowledge time -
    /// "the number as the terminal shows it today".  This is the UNGUARDED
    /// read every beginner's pandas merge performs silently; the demo's
    /// naive strategy does it loudly, once, so the consequence is visible.
    pub fn latest(&self, key: &str) -> Option<&Entry> {
        self.history(key).last()
    }

    /// A guarded window onto this ledger, bound to `clock`.
    pub fn view<'a>(&'a self, clock: &'a Clock) -> AsOfView<'a> {
        AsOfView {
            ledger: self,
            clock,
        }
    }
}

// ---------------------------------------------------------------------------
// AsOfView
// ---------------------------------------------------------------------------

/// A window on a [`Ledger`] that refuses to show the future.
///
/// This is what strategies receive.  Every read is stamped against the
/// view's clock; a read of data first knowable later than now is a
/// [`ReadError::Leak`].  There is deliberately no method to bypass the
/// guard - the raw side lives on the ledger, and handing a strategy the
/// ledger is a visible, blameable act (the demo does exactly that on
/// purpose, once).
pub struct AsOfView<'a> {
    ledger: &'a Ledger,
    clock: &'a Clock,
}

impl<'a> AsOfView<'a> {
    /// The instant this view is reading at.
    pub fn as_of(&self) -> Time {
        self.clock.now()
    }

    /// The clock this view is bound to (shared with the replay engine).
    pub fn clock(&self) -> &Clock {
        self.clock
    }

    /// The raw ledger behind the guard.  Present so the demo can hand the
    /// naive strategy exactly the object it deserves; a strategy holding
    /// this has left the honest path, visibly.
    pub fn raw_ledger(&self) -> &'a Ledger {
        self.ledger
    }

    /// Honest read.  [`ReadError::Leak`] if the key's first revision is in
    /// the future (the guard firing); [`ReadError::NotYetKnown`] if the key
    /// does not exist at all.
    pub fn get(&self, key: &str) -> Result<&Value, ReadError> {
        let when = self.clock.now();
        let prior = self.ledger.history(key);
        for entry in prior.iter().rev() {
            if entry.at(when) {
                return Ok(&entry.value);
            }
        }
        if prior.is_empty() {
            Err(ReadError::NotYetKnown {
                key: key.to_string(),
            })
        } else {
            // The key exists but the clock hasn't seen it: reading it now
            // would be a leak.  Report the FIRST revision's knowledge time -
            // the earliest moment any version of this datum existed.
            Err(ReadError::Leak {
                key: key.to_string(),
                knowledge_time: prior[0].knowledge_time,
                as_of: when,
            })
        }
    }

    /// [`AsOfView::get`] with the value cloned out, for callers that want to
    /// keep it without holding the borrow.
    pub fn get_owned(&self, key: &str) -> Result<Value, ReadError> {
        self.get(key).cloned()
    }

    /// The read as an Option: leaks and unknowns both come back None.
    /// For strategies that treat "no honest answer yet" as one state.
    pub fn try_get(&self, key: &str) -> Option<&Value> {
        self.get(key).ok()
    }

    /// Number-or-default read: like Python's `get_or`, both a leak and an
    /// unknown resolve to `default`.  If you want the leak loud, use
    /// [`AsOfView::get`] - this one is for strategies with a stated
    /// fall-back rule.
    pub fn get_num_or(&self, key: &str, default: f64) -> f64 {
        self.try_get(key).and_then(Value::as_num).unwrap_or(default)
    }

    /// Keys with at least one revision knowable now.
    ///
    /// Iterate over THIS, never over `ledger.keys()`: the ledger's full key
    /// list includes companies that hadn't listed yet and series that
    /// started later - iterating it is survivorship bias with extra steps.
    pub fn known_keys(&self) -> Vec<String> {
        let when = self.clock.now();
        self.ledger
            .by_key
            .iter()
            .filter(|(_, revs)| revs.first().is_some_and(|e| e.knowledge_time <= when))
            .map(|(k, _)| k.clone())
            .collect()
    }

    /// Revisions of `key` knowable now (a restatement in progress shows up
    /// here).
    pub fn revisions(&self, key: &str) -> Vec<&Entry> {
        let when = self.clock.now();
        self.ledger
            .history(key)
            .iter()
            .filter(|e| e.at(when))
            .collect()
    }

    /// Does the guard admit this key right now?
    pub fn contains(&self, key: &str) -> bool {
        self.get(key).is_ok()
    }
}

// ---------------------------------------------------------------------------
// unit tests: the calendar and the parser are the two things here worth
// pinning to known anchors.
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn civil_calendar_roundtrip_and_anchors() {
        // Anchors first: day 0 is 1970-01-01, the millenium rollover, and a
        // leap day that exists.
        assert_eq!(days_from_civil(1970, 1, 1), 0);
        assert_eq!(civil_from_days(0), (1970, 1, 1));
        assert_eq!(days_from_civil(2000, 3, 1), 11017);
        assert_eq!(civil_from_days(11017), (2000, 3, 1));
        assert_eq!(civil_from_days(days_from_civil(2024, 2, 29)), (2024, 2, 29));
        // Roundtrip every 97 days across three millennia.
        for z in (-719_468..=3_000_000).step_by(97) {
            let (y, m, d) = civil_from_days(z);
            assert_eq!(days_from_civil(y, m, d), z, "roundtrip at day {z}");
        }
    }

    #[test]
    fn month_lengths_respect_leap_years() {
        assert_eq!(days_in_month(2023, 2), 28);
        assert_eq!(days_in_month(2024, 2), 29);
        assert_eq!(days_in_month(2000, 2), 29); // div by 400
        assert_eq!(days_in_month(1900, 2), 28); // div by 100, not 400
        assert_eq!(days_in_month(2023, 4), 30);
        assert_eq!(days_in_month(2023, 12), 31);
    }

    #[test]
    fn weekday_anchors() {
        // 1970-01-01 Thursday(3); 2023-12-31 Sunday(6); 2024-01-02 Tuesday(1).
        let t = |s: &str| Time::parse(s).expect("parse");
        assert_eq!(t("1970-01-01").weekday(), 3);
        assert_eq!(t("2023-12-31").weekday(), 6);
        assert_eq!(t("2024-01-02").weekday(), 1);
    }

    #[test]
    fn parse_accepts_the_documented_spellings() {
        let expected = Time::parse("2024-03-01T14:00:00").expect("naive");
        assert_eq!(Time::parse("2024-03-01T14:00:00Z").expect("z"), expected);
        assert_eq!(
            Time::parse("2024-03-01T14:00:00+00:00").expect("offset"),
            expected
        );
        assert_eq!(Time::parse("2024-03-01 14:00:00").expect("space"), expected);
        assert_eq!(
            Time::parse("2024-03-01T14:00").expect("no seconds"),
            expected
        );
        // Date-only is midnight of that day, UTC.
        assert_eq!(
            Time::parse("2024-03-01").expect("date only"),
            Time::parse("2024-03-01T00:00:00").expect("midnight")
        );
        let frac = Time::parse("2024-03-01T14:00:00.5").expect("frac");
        assert_eq!(frac.as_nanos(), expected.as_nanos() + 500_000_000);
        assert_eq!(frac.to_iso(), "2024-03-01T14:00:00.500000+00:00");
    }

    #[test]
    fn parse_rejects_the_promised_rejects() {
        for bad in [
            "2023-02-29",          // not a leap year
            "2023-13-01",          // month 13
            "2023-00-10",          // month 0
            "2023-04-31",          // April has 30
            "2024-01-02T24:00:00", // hour 24
            "2024-01-02T10:61:00", // minute 61
            "2024-01-02T10:00:60", // second 60
            "2024-01-02T10:00",    // fine actually - exclude below
        ] {
            if bad == "2024-01-02T10:00" {
                assert!(Time::parse(bad).is_ok());
                continue;
            }
            assert!(Time::parse(bad).is_err(), "{bad} should be rejected");
        }
        assert!(Time::parse("2024-01-02T10:00:00+05:00").is_err()); // only UTC
        assert!(Time::parse("").is_err());
        assert!(Time::parse("not-a-time").is_err());
    }

    #[test]
    fn leak_message_carries_the_full_story() {
        let kt = Time::parse("2024-03-01T14:00:00").expect("kt");
        let as_of = Time::parse("2024-02-15T20:00:00").expect("as_of");
        let err = ReadError::Leak {
            key: "SYM2.revenue".to_string(),
            knowledge_time: kt,
            as_of,
        };
        let text = err.to_string();
        assert!(text.contains("LEAK via \"SYM2.revenue\""));
        assert!(text.contains("2024-03-01T14:00:00+00:00"));
        assert!(text.contains("2024-02-15T20:00:00+00:00"));
        assert!(text.contains("(14 days, 18:00:00) into the future"));
    }

    #[test]
    fn clock_is_monotonic_and_forks_are_independent() {
        let clock = Clock::at(Time::parse("2024-01-02").expect("start"));
        let later = clock
            .advance_to(Time::parse("2024-03-01").expect("t"))
            .expect("ok");
        assert_eq!(clock.now(), later);
        let rewind = clock.advance_to(Time::parse("2024-02-15").expect("t"));
        assert!(rewind.is_err());
        assert_eq!(clock.now(), later, "failed advance must not move the clock");

        let child = clock.fork();
        child
            .advance_to(Time::parse("2025-01-01").expect("t"))
            .expect("ok");
        assert_eq!(clock.now(), later, "fork must not move the parent");
        assert_eq!(child.now(), Time::parse("2025-01-01").expect("t"));
    }
}
