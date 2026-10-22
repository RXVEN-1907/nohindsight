#!/usr/bin/env python3
"""Inspect leak_chorus.wav with real audio tools.

librosa computes an RMS envelope and a spectral centroid over time. The
centroid carries the story: during the leak window the two tones sit close
to unison near 110 Hz, warm and low. At knowledge_time, 2024-03-01, the
naive tone drops its pin and jumps to its own pitch near 194 Hz, and the
centroid leaps. This script prints the frames where the centroid moves
most. Those are the reveal, in numbers. The 5 Hz beat inside the window
shows up as a small wobble in both curves, right before the jump.

This script is not run in CI. Install librosa first.

    pip install librosa
    python gallery/audio/analyze_leak.py

pydub variant, for mp3 export, if mp3 is what the delivery needs:

    from pydub import AudioSegment

    seg = AudioSegment.from_wav("gallery/audio/leak_chorus.wav")
    seg.export("gallery/audio/leak_chorus.mp3", format="mp3", bitrate="192k")

    # pydub needs ffmpeg on PATH for mp3 encoding. ffmpeg is already
    # installed here, so the export is one call.
"""

import argparse
import json
from datetime import date
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_WAV = SCRIPT_DIR / "leak_chorus.wav"
DATA_PATH = SCRIPT_DIR.parent / "data" / "leak_demo.json"


def leak_window_dates():
    with open(DATA_PATH, "r") as f:
        data = json.load(f)
    detail = data["leak"]["detail"]
    return date.fromisoformat(detail["first_leak_day"]), date.fromisoformat(detail["knowledge_time"])


def main():
    ap = argparse.ArgumentParser(description="Analyze leak_chorus.wav.")
    ap.add_argument("--wav", default=str(DEFAULT_WAV),
                    help="path to the chorus (default: gallery/audio/leak_chorus.wav)")
    args = ap.parse_args()
    wav_path = Path(args.wav)

    try:
        import librosa
        import numpy
    except ImportError as exc:
        print("librosa is missing. Install it: pip install librosa")
        print("detail: %s" % exc)
        return 1
    if not wav_path.exists():
        print("no file at %s. Run gallery/audio/sonify.py first." % wav_path)
        return 1

    y, sr = librosa.load(str(wav_path), sr=None, mono=True)
    rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=512)[0]
    centroid = librosa.feature.spectral_centroid(y=y, sr=sr, n_fft=2048, hop_length=512)[0]
    times = librosa.times_like(centroid, sr=sr, hop_length=512)

    print("file: %s" % wav_path)
    print("duration: %.2f s, sample rate %d Hz, %d analysis frames"
          % (len(y) / sr, sr, len(centroid)))
    print()
    print("rms envelope: mean %.4f, max %.4f at t=%.2f s, min %.4f at t=%.2f s"
          % (float(numpy.mean(rms)), float(numpy.max(rms)),
             float(times[int(numpy.argmax(rms))]), float(numpy.min(rms)),
             float(times[int(numpy.argmin(rms))])))
    print("spectral centroid: mean %.0f Hz, min %.0f Hz at t=%.2f s, max %.0f Hz at t=%.2f s"
          % (float(numpy.mean(centroid)), float(numpy.min(centroid)),
             float(times[int(numpy.argmin(centroid))]), float(numpy.max(centroid)),
             float(times[int(numpy.argmax(centroid))])))

    lo, hi = leak_window_dates()
    print()
    print("leak window from the demo data: %s to %s" % (lo, hi))
    print("largest centroid shifts, frame to frame:")
    shifts = numpy.abs(numpy.diff(centroid))
    top = sorted(numpy.argsort(shifts)[::-1][:5])
    for idx in top:
        print("  t=%.2f s: %.0f Hz to %.0f Hz in one hop"
              % (float(times[idx]), float(centroid[idx]), float(centroid[idx + 1])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
