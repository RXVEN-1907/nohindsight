//! nohindsight rng — one deterministic stream, seeded, documented.
//!
//! Every random number in the engine comes from a [`SplitMix64`]: the same
//! seed builds the same world, run after run, on every platform.  No `rand`
//! crate, no OS entropy in anything deterministic — the only entropy in this
//! crate is behind [`SplitMix64::from_os`] for callers who genuinely want it
//! (none of the library paths do).
//!
//! DRAW ORDER CONTRACT — every function that draws states its order, and the
//! generators in [`crate::synth`] follow it:
//!
//! * [`SplitMix64::next_u64`] advances the 64-bit state once and decorates
//!   it (splitmix64, Steele et al.) — the atomic draw everything else uses;
//! * [`SplitMix64::next_f64`] = one `next_u64`, top 53 bits, scaled to
//!   [0, 1);
//! * [`SplitMix64::gauss`] = exactly two `next_f64` draws (Box-Muller, cos
//!   form), redrawn only if the first lands on exactly 0.0;
//! * [`SplitMix64::uniform(lo, hi)`] = one `next_f64`, mapped into [lo, hi);
//! * [`SplitMix64::below(p)`] = one `next_f64`, compared against `p`;
//! * [`SplitMix64::randrange(n)`] = rejection sampling on `next_u64` —
//!   usually one draw, occasionally more; the count never affects any other
//!   stream because each generator owns its own `SplitMix64`.
//!
//! The calendar helpers used by the seeded generators live with
//! [`crate::core::Time`] (`weekday`, `plus_days`, date arithmetic); this
//! module is the single source of randomness and nothing else.

use std::time::{SystemTime, UNIX_EPOCH};

/// splitmix64: 64 bits of state, one mix per draw, passes every statistical
/// battery the engine's purposes need (it is the reference seedsman for
/// xoshiro family testing).  Not cryptographic — nothing here needs to
/// secret anything.
#[derive(Clone, Debug)]
pub struct SplitMix64 {
    state: u64,
}

impl SplitMix64 {
    /// A stream seeded exactly — the constructor all deterministic paths use.
    pub fn new(seed: u64) -> SplitMix64 {
        SplitMix64 { state: seed }
    }

    /// A stream seeded from the OS clock, for callers that want novelty
    /// (interactive play, not tests).
    pub fn from_os() -> SplitMix64 {
        let nanos = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_or(0x9E37_79B9_7F4A_7C15, |d| d.as_nanos() as u64);
        SplitMix64::new(nanos)
    }

    /// The atomic draw: advance the state, then avalanche it.
    pub fn next_u64(&mut self) -> u64 {
        self.state = self.state.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.state;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }

    /// Uniform [0, 1) with 53 bits of mantissa — the same resolution as
    /// Python's `random.random`.
    pub fn next_f64(&mut self) -> f64 {
        (self.next_u64() >> 11) as f64 * (1.0 / (1u64 << 53) as f64)
    }

    /// Uniform [lo, hi).  Assumes lo <= hi (a swapped range returns lo..
    /// hi's worth of negative noise; the generators never do that).
    pub fn uniform(&mut self, lo: f64, hi: f64) -> f64 {
        lo + (hi - lo) * self.next_f64()
    }

    /// True with probability p (one draw).
    pub fn below(&mut self, p: f64) -> bool {
        self.next_f64() < p
    }

    /// Uniform integer in [0, n) by rejection — no modulo bias.  Panics on
    /// n == 0 (calling it is a bug, not a draw).
    pub fn randrange(&mut self, n: u64) -> u64 {
        assert!(n > 0, "randrange(0) has no answer");
        if n == 1 {
            return 0;
        }
        let zone = u64::MAX / n * n; // largest multiple of n <= u64::MAX
        loop {
            let x = self.next_u64();
            if x < zone {
                return x % n;
            }
        }
    }

    /// Normal(mean, std) via Box-Muller, cos form: exactly two `next_f64`
    /// draws per return (u1 powers the radius, u2 the angle).  u1 == 0.0
    /// would send the log to -inf, so it is redrawn — the probability is
    /// 2^-53, below anything measurable, but the guard is cheap.
    pub fn gauss(&mut self, mean: f64, std: f64) -> f64 {
        let mut u1 = self.next_f64();
        while u1 <= 0.0 {
            u1 = self.next_f64();
        }
        let u2 = self.next_f64();
        let r = (-2.0 * u1.ln()).sqrt();
        let theta = std::f64::consts::TAU * u2;
        mean + std * r * theta.cos()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn same_seed_same_stream() {
        let mut a = SplitMix64::new(7);
        let mut b = SplitMix64::new(7);
        for _ in 0..1000 {
            assert_eq!(a.next_u64(), b.next_u64());
        }
    }

    #[test]
    fn floats_stay_in_range() {
        let mut rng = SplitMix64::new(42);
        for _ in 0..10_000 {
            let x = rng.next_f64();
            assert!((0.0..1.0).contains(&x));
            let u = rng.uniform(-3.0, 5.0);
            assert!((-3.0..5.0).contains(&u));
        }
    }

    #[test]
    fn gauss_draws_two_per_call_and_centers_where_asked() {
        let mut rng = SplitMix64::new(11);
        // Known first draws: pin the Box-Muller order so a refactor cannot
        // silently change the world every seeded generator builds.
        let first = rng.gauss(0.0, 1.0);
        let mut taps = SplitMix64::new(11);
        let u1 = taps.next_f64();
        let u2 = taps.next_f64();
        let expected = (-2.0 * u1.ln()).sqrt() * (std::f64::consts::TAU * u2).cos();
        assert_eq!(first, expected);

        let mut rng = SplitMix64::new(3);
        let n = 200_000;
        let mut sum = 0.0;
        let mut sumsq = 0.0;
        for _ in 0..n {
            let g = rng.gauss(5.0, 2.0);
            sum += g;
            sumsq += g * g;
        }
        let mean = sum / n as f64;
        let var = sumsq / n as f64 - mean * mean;
        assert!((mean - 5.0).abs() < 0.05, "mean {mean}");
        assert!((var - 4.0).abs() < 0.2, "var {var}");
    }

    #[test]
    fn randrange_is_unbiased_enough_and_bounded() {
        let mut rng = SplitMix64::new(9);
        let (n, trials) = (7u64, 70_000);
        let mut counts = vec![0u64; n as usize];
        for _ in 0..trials {
            let r = rng.randrange(n);
            assert!(r < n);
            counts[r as usize] += 1;
        }
        let expected = trials as f64 / n as f64;
        for (i, c) in counts.iter().enumerate() {
            let rel = (*c as f64 - expected) / expected;
            assert!(rel.abs() < 0.05, "bucket {i} off by {rel:.3}");
        }
    }
}
