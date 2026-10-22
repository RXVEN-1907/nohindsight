#!/usr/bin/env python3
"""Sonify the nohindsight restatement leak.

Reads gallery/data/leak_demo.json and renders the demo's two equity curves
as 16-bit mono WAV, 22050 Hz. Stdlib only. Deterministic. Three files:

  honest.wav       one tone tracking the honest curve. The baseline.
  naive.wav        the same pitch mapping on the naive curve: the path that
                   read restated revenue from the terminal snapshot.
  leak_chorus.wav  both tones mixed. During the leak window the naive tone
                   is pinned just above the honest one and drifts sharp.
                   Two sines at f and f + d produce amplitude modulation at
                   rate d. That beat is the point: the naive path sounds
                   sweet, then the wobble says something was off.

Pitch mapping: log2(equity / 100000) scaled linearly into 110 to 440 Hz,
A2 to A4, two octaves. Log first, then pitch: ears hear ratios, so a
strategy that doubles its equity should rise by octaves, not by a fixed
number of hertz.

Beat math, stated once: sin(2*pi*f*t) + sin(2*pi*(f+d)*t) equals
2*sin(2*pi*(f+d/2)*t)*cos(pi*d*t). The envelope repeats at rate d. The
detune here ramps from 0 to 75 cents across the leak window. The honest
tone sits near 113 Hz at the window edge, so d climbs from 0 to about
5.0 Hz: five wobbles a second, unmistakable. Harmonics of each tone beat
at integer multiples of d, which thickens the roughness.

The window is 2024-01-02 to 2024-03-01, the first six curve points, so the
beat section is the first 1.3 seconds of the chorus. At knowledge_time the
naive tone drops the pin and jumps to its natural pitch, several semitones
up: the fake edge, audible for the rest of the track. The two voices then
wander together and never reconcile. That is the data.

Usage: python gallery/audio/sonify.py [--out DIR]
"""

import argparse
import json
import math
import struct
import wave
from datetime import date
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_PATH = SCRIPT_DIR.parent / "data" / "leak_demo.json"

SAMPLE_RATE = 22050
TOTAL_SECONDS = 25.0
TARGET_PEAK = 0.80       # headroom under the 0.9 clipping line
PITCH_LOW_HZ = 110.0     # A2
PITCH_HIGH_HZ = 440.0    # A4. 110 to 440 Hz, two octaves.
ATTACK_SECONDS = 0.008   # short linear fade-in per segment: no clicks
RELEASE_SECONDS = 0.008  # short linear fade-out per segment: no clicks
HARMONICS = ((1, 1.0), (2, 0.20), (3, 0.10))  # sine with a little warmth
DETUNE_CENTS = 75.0      # how far the naive tone drifts sharp, at most


def load_curve():
    with open(DATA_PATH, "r") as f:
        data = json.load(f)
    points = data["curve"]
    detail = data["leak"]["detail"]
    dates = [date.fromisoformat(p["date"]) for p in points]
    honest = [float(p["honest"]) for p in points]
    naive = [float(p["naive"]) for p in points]
    window_lo = date.fromisoformat(detail["first_leak_day"])
    window_hi = date.fromisoformat(detail["knowledge_time"])
    return dates, honest, naive, window_lo, window_hi


def pitch_freqs(equities, lo, hi):
    # Map log-equity linearly onto log-frequency across the shared range.
    out = []
    for eq in equities:
        le = math.log2(eq / 100000.0)
        t = (le - lo) / (hi - lo)
        out.append(PITCH_LOW_HZ * 2.0 ** ((math.log2(PITCH_HIGH_HZ / PITCH_LOW_HZ)) * t))
    return out


def render(freqs, seg_samples, env):
    """One segment per point, gliding between pitches, phase continuous.

    Frequency is integrated into a running phase, so a moving pitch never
    discontinues and never clicks on its own. The linear attack and release
    per segment handle the boundaries.
    """
    n = len(freqs)
    total = seg_samples * n
    out = [0.0] * total
    two_pi_over_sr = 2.0 * math.pi / SAMPLE_RATE
    sin = math.sin
    phase = 0.0
    for seg in range(n):
        f0 = freqs[seg]
        f1 = freqs[seg + 1] if seg + 1 < n else freqs[seg]
        base = seg * seg_samples
        for j in range(seg_samples):
            f = f0 + (f1 - f0) * (j / seg_samples)
            phase += two_pi_over_sr * f
            s = 0.0
            for k, amp in HARMONICS:
                s += amp * sin(k * phase)
            out[base + j] = s * env[j]
    return out


def make_env(seg_samples):
    attack = max(1, int(ATTACK_SECONDS * SAMPLE_RATE))
    release = max(1, int(RELEASE_SECONDS * SAMPLE_RATE))
    env = [1.0] * seg_samples
    for i in range(min(attack, seg_samples)):
        env[i] = i / attack
    for i in range(min(release, seg_samples)):
        env[seg_samples - 1 - i] = i / release
    return env


def normalize(samples):
    peak = max(abs(s) for s in samples)
    if peak == 0.0:
        return list(samples)
    scale = TARGET_PEAK / peak
    return [s * scale for s in samples]


def write_wav(path, samples):
    ints = []
    for s in samples:
        v = max(-1.0, min(1.0, s))
        ints.append(int(v * 32767.0))
    w = wave.open(str(path), "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(SAMPLE_RATE)
    w.writeframes(struct.pack("<%dh" % len(ints), *ints))
    w.close()


def report(path):
    # Open the finished file back up with the wave module and state facts.
    with wave.open(str(path), "rb") as w:
        frames = w.getnframes()
        sr = w.getframerate()
        raw = w.readframes(frames)
    ints = struct.unpack("<%dh" % frames, raw)
    peak = max(abs(i) for i in ints) / 32767.0
    print("wrote %s: %d bytes, %d frames, %.3f s, peak %.3f"
          % (path.name, path.stat().st_size, frames, frames / sr, peak))


def main():
    ap = argparse.ArgumentParser(description="Render the leak demo as audio.")
    ap.add_argument("--out", default=str(SCRIPT_DIR),
                    help="output directory (default: gallery/audio)")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    dates, honest_eq, naive_eq, window_lo, window_hi = load_curve()
    print("read %s: %d points, %s to %s"
          % (DATA_PATH.relative_to(SCRIPT_DIR.parent.parent), len(dates),
             dates[0], dates[-1]))

    logs = [math.log2(v / 100000.0) for v in honest_eq + naive_eq]
    lo, hi = min(logs), max(logs)
    f_honest = pitch_freqs(honest_eq, lo, hi)
    f_naive = pitch_freqs(naive_eq, lo, hi)

    # Leak window: the naive tone hides behind the honest contour, one
    # fixed detune above it, ramping in. The real naive curve rose during
    # the window; the pin is deliberate. It makes the beat the whole show
    # and leaves the jump to knowledge_time as the reveal.
    window_idx = [i for i, d in enumerate(dates) if window_lo <= d <= window_hi]
    f_naive_pinned = list(f_naive)
    for pos, i in enumerate(window_idx):
        ramp = pos / max(1, len(window_idx) - 1)
        f_naive_pinned[i] = f_honest[i] * 2.0 ** (ramp * DETUNE_CENTS / 1200.0)

    seg_samples = int(TOTAL_SECONDS * SAMPLE_RATE) // len(dates)
    env = make_env(seg_samples)

    honest_track = normalize(render(f_honest, seg_samples, env))
    naive_track = normalize(render(f_naive, seg_samples, env))
    pinned_track = normalize(render(f_naive_pinned, seg_samples, env))
    # Equal weights. Neither voice owns the room.
    chorus = normalize([0.5 * a + 0.5 * b
                        for a, b in zip(honest_track, pinned_track)])

    write_wav(out / "honest.wav", honest_track)
    write_wav(out / "naive.wav", naive_track)
    write_wav(out / "leak_chorus.wav", chorus)
    print()
    report(out / "honest.wav")
    report(out / "naive.wav")
    report(out / "leak_chorus.wav")
    print()
    print("leak window: %s to %s, %d of %d points pinned"
          % (window_lo, window_hi, len(window_idx), len(dates)))
    if window_idx:
        last = window_idx[-1]
        d_hz = f_honest[last] * (2.0 ** (DETUNE_CENTS / 1200.0) - 1.0)
        print("detune: 0 to %d cents across the window. Beat rate d ends at "
              "%.1f Hz on a %.1f Hz tone." % (DETUNE_CENTS, d_hz, f_honest[last]))
        reveal = last * seg_samples / SAMPLE_RATE
        print("reveal: t=%.2f s, the naive tone drops the pin and jumps to "
              "its own pitch (%.0f to %.0f Hz)."
              % (reveal, f_naive_pinned[last], f_naive[last + 1]))
    print("done: 3 files in %s" % out)


if __name__ == "__main__":
    main()
