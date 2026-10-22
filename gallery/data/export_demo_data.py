"""Export the demo's real numbers to JSON for the gallery.

Every chart in gallery/ (matplotlib, Chart.js, Three.js, audio, MIDI)
renders from this one file, so the story stays identical everywhere.

    python gallery/data/export_demo_data.py

Deterministic: same seeds as the demo, same bytes every run. Stdlib only.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

# allow running from anywhere: repo root is two levels up
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from nohindsight.demo import run  # noqa: E402

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OUT = os.path.join(REPO, "gallery", "data", "leak_demo.json")


def downsample(curve, points=90):
    """Every k-th sample plus the last one; curves are ~750 bars."""
    step = max(1, len(curve) // points)
    picked = curve[::step]
    if picked[-1] != curve[-1]:
        picked.append(curve[-1])
    return picked


def num(x, ndigits=4):
    """NaN to null: strict JSON for every consumer (browsers included)."""
    x = round(float(x), ndigits)
    return None if x != x else x


def verdict_summary(v):
    return {
        "level": v.level,
        "sharpe": num(v.numbers.get("sharpe", float("nan")), 3),
        "dsr_prob": num(v.numbers.get("dsr_prob", float("nan"))),
        "permutation_p": num(v.numbers.get("permutation_p", float("nan"))),
        "max_drawdown": num(v.numbers.get("max_drawdown", float("nan"))),
        "reasons": v.reasons,
    }


def main():
    with tempfile.TemporaryDirectory() as tmp:
        result = run(out_dir=tmp, verbose=False)

    payload = {
        "meta": {
            "generator": "gallery/data/export_demo_data.py",
            "note": "regenerate with: python gallery/data/export_demo_data.py",
            "seeds": {"price": 455, "fundamentals": 7, "fills": 7},
        },
        "honest": verdict_summary(result["honest_verdict"]),
        "naive": verdict_summary(result["naive_verdict"]),
        "leak": {
            "code": result["leak_finding"].code,
            "severity": str(result["leak_finding"].severity),
            "message": result["leak_finding"].message,
            "detail": {k: str(v) for k, v in result["leak_finding"].detail.items()},
        },
        "split_caught": bool(result["split_caught"]),
        "curve": [
            {"date": ts.strftime("%Y-%m-%d"), "honest": round(h, 2), "naive": round(n, 2)}
            for (ts, h), (_, n) in zip(downsample(result["honest_curve"]), downsample(result["naive_curve"]))
        ],
    }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump(payload, f, indent=1)
        f.write("\n")
    print(f"wrote {OUT}")
    print(f"honest: {payload['honest']['level']} sharpe {payload['honest']['sharpe']}")
    print(f"naive:  {payload['naive']['level']} sharpe {payload['naive']['sharpe']}")
    print(f"curve points: {len(payload['curve'])}")


if __name__ == "__main__":
    main()
