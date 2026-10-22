//! nohindsight engine — "Python is the research API.  Rust is the engine."
//!
//! The Python package (`nohindsight/`) is the research API: easy to poke at,
//! slow to run at scale.  This crate is the same ideas as a fast native
//! binary + library.  Same two-clock model, same forensics formulas, same
//! planted-crime synthetic data, same closing line — std only, zero
//! dependencies, deterministic given seeds.
//!
//! Modules, and their Python counterparts:
//!
//! * [`core`] — the two clocks and the guard (`nohindsight/core.py`)
//! * [`rng`] — seeded splitmix64, every random stream in the crate
//! * [`stats`] — the five classical numbers + the normal functions
//! * [`forensics`] — PSR / DSR / permutation test / the verdict
//! * [`pbo`] — probability of backtest overfitting via CSCV
//! * [`costs`] — the reality tax, itemized (fees/spread/impact/borrow)
//! * [`synth`] — a small world with crimes planted in it
//! * [`checks`] — the bar-level police (splits, gaps, stale, duplicates)
//! * [`csv`] — a small line-based CSV reader for the CLI's input paths
//! * [`demo`] — the whole story in one run, ending in the closing line
//!
//! The `nh` binary is the clap-free front door: `nh version`, `nh demo`,
//! `nh audit <csv>`, `nh check <csv>`.

#![deny(warnings)]

pub mod checks;
pub mod core;
pub mod costs;
pub mod csv;
pub mod demo;
pub mod forensics;
pub mod pbo;
pub mod rng;
pub mod stats;
pub mod synth;

/// Crate version, straight from Cargo.toml — `nh version` prints it.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");
