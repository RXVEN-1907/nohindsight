"""Render the leak animation with real typography, via matplotlib.

    python3.10 tools/render_leak_gif.py [FRAMES_DIR]

360 frames, 1600x900, 30 fps, 12 s. The act:
    0.00-0.28  honest curve draws on (lavender, glow)
    0.25-0.55  naive curve draws on (crimson, glow)
    0.50-0.65  the leak window blooms
    0.55-0.80  the naive curve inside the window turns DASHED and marches
               (that segment is built on data the world did not have);
               a crimson scan line sweeps the band once;
               the counter ticks up the days priced early
    0.78-0.90  verdict chips land on the curve ends
    0.90-1.00  hold, dashes keep marching

Stdlib + matplotlib only. Deterministic.
"""

from __future__ import annotations

import json
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D  # noqa: F401 (kept for clarity)

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, ".."))
DATA = os.path.join(REPO, "docs", "data", "leak_demo.json")

BG = "#0B0C10"
HAIR = "#2A2D3A"
MUTED = "#8A8FA3"
GHOST = "#4A4E63"
LAVENDER = "#D0C4DF"
LAVENDER_DEEP = "#705EB1"
CRIMSON = "#E5484D"
SULFUR = "#E6B455"
TEXT = "#F2F3F7"

plt.rcParams["font.family"] = "DejaVu Sans Mono"


def ease(x):
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) ** 3


def load():
    with open(DATA) as f:
        d = json.load(f)
    return d


def build_frame(d, t, path):
    n = len(d["curve"])
    xs = list(range(n))
    honest = [p["honest"] for p in d["curve"]]
    naive = [p["naive"] for p in d["curve"]]
    dates = [p["date"] for p in d["curve"]]
    i0 = next(i for i, dt in enumerate(dates) if dt >= "2024-01-02")
    i1 = next(i for i, dt in enumerate(dates) if dt >= "2024-03-01")

    fig = plt.figure(figsize=(16, 9), dpi=100, facecolor=BG)
    ax = fig.add_axes([0.07, 0.12, 0.86, 0.76])
    ax.set_facecolor(BG)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(HAIR)
    ax.set_xlim(0, n - 1)
    ax.set_ylim(96_000, 147_000)
    ax.set_yticks([100_000, 120_000, 140_000])
    ax.set_yticklabels(["$100k", "$120k", "$140k"], color=MUTED, fontsize=13)
    tick_idx = list(range(0, n, 11))
    if tick_idx[-1] != n - 1:
        tick_idx.append(n - 1)
    ax.set_xticks(tick_idx)
    ax.set_xticklabels([dates[i][:7] for i in tick_idx], color=MUTED, fontsize=12)
    ax.tick_params(colors=HAIR, length=4)
    ax.grid(axis="y", color=HAIR, lw=0.7, alpha=0.6)

    # leak window
    band_a = 0.10 + 0.05 * abs(((t * 3.0) % 1.0) - 0.5) * 2 if t > 0.50 else 0.0
    if t > 0.50:
        ax.axvspan(i0, i1, color=CRIMSON, alpha=ease((t - 0.50) / 0.15) * band_a, lw=0)
        for xi in (i0, i1):
            ax.axvline(xi, color=CRIMSON, alpha=0.5, lw=1.0, ls=(0, (3, 4)))
        ax.text((i0 + i1) / 2, 144_500, "THE LEAK WINDOW", color=CRIMSON,
                fontsize=15, ha="center", weight="bold")

    def drawn(vals, frac):
        m = max(2, int(n * ease(frac)))
        return xs[:m], vals[:m]

    # honest: glow pass then crisp pass
    fx, fy = drawn(honest, min(1.0, t / 0.28))
    ax.plot(fx, fy, color=LAVENDER, lw=7, alpha=0.12, solid_capstyle="round")
    ax.plot(fx, fy, color=LAVENDER, lw=3.2, alpha=0.30, solid_capstyle="round")
    ax.plot(fx, fy, color=LAVENDER, lw=1.5, solid_capstyle="round")

    # naive: glow pass, crisp pass; the in-window segment is dashed + marching
    fn = min(1.0, max(0.0, (t - 0.25) / 0.30))
    mx = max(2, int(n * ease(fn)))
    pre_end = min(mx, i0)
    post_start = min(mx, i1)
    ax.plot(xs[:pre_end], naive[:pre_end], color=CRIMSON, lw=7, alpha=0.12, solid_capstyle="round")
    ax.plot(xs[:pre_end], naive[:pre_end], color=CRIMSON, lw=3.2, alpha=0.30)
    ax.plot(xs[:pre_end], naive[:pre_end], color=CRIMSON, lw=1.5)
    ax.plot(xs[post_start:], naive[post_start:], color=CRIMSON, lw=7, alpha=0.12)
    ax.plot(xs[post_start:], naive[post_start:], color=CRIMSON, lw=1.5)
    if mx > i0 and mx > 2:  # the cheated segment, marching dashes
        seg_end = min(mx, i1)
        if seg_end > i0 + 1:
            ax.plot(xs[i0:seg_end], naive[i0:seg_end], color=CRIMSON, lw=2.2,
                    ls=(-int(t * 360) % 10, (6, 4)))

    # scan line sweeping the band once
    if 0.55 < t < 0.80:
        sx = i0 + (i1 - i0) * ease((t - 0.55) / 0.25)
        ax.axvline(sx, color=CRIMSON, alpha=0.75, lw=1.6)

    # counter
    if t > 0.55:
        frac_in = min(1.0, (t - 0.55) / 0.25)
        days = int(round(43 * frac_in))
        c = CRIMSON if frac_in < 1 else MUTED
        ax.text(0.99, 0.985, f"decision days priced early: {days:2d} / 43",
                transform=ax.transAxes, color=c, fontsize=15,
                ha="right", va="top", weight="bold")

    # curve-end name tags
    if t > 0.35:
        ax.text(min(n - 1, mx - 1) + 1.5, naive[min(mx - 1, n - 1)], "naive",
                color=CRIMSON, fontsize=14, va="center")
    if t > 0.10:
        hx = min(n - 1, int(n * ease(min(1.0, t / 0.28))) - 1)
        ax.text(hx + 1.5, honest[max(0, hx)], "honest", color=LAVENDER,
                fontsize=14, va="center")

    # verdict chips
    if t > 0.78:
        k = ease((t - 0.78) / 0.12)
        ax.text(n - 1 + 3, naive[-1] + 2500, "naive · REAL · sharpe 1.17",
                color=CRIMSON, fontsize=14.5, ha="right", weight="bold",
                bbox=dict(boxstyle="round,pad=0.45", fc="#12141C", ec=CRIMSON, lw=1.4))
        ax.text(n - 1 + 3, honest[-1] - 6000, "honest · SUSPICIOUS · sharpe 0.69",
                color=LAVENDER, fontsize=14.5, ha="right", weight="bold",
                bbox=dict(boxstyle="round,pad=0.45", fc="#12141C", ec=LAVENDER, lw=1.4))

    # header + footer
    fig.text(0.055, 0.945, "no", color=LAVENDER, fontsize=26, weight="bold")
    fig.text(0.078, 0.945, "hindsight", color=TEXT, fontsize=26, weight="bold")
    fig.text(0.155, 0.951, "the leak, frame by frame", color=MUTED, fontsize=15)
    fig.text(0.055, 0.035,
             "43 decision days priced off a revision first published 2024-03-01 · up to 58 days early",
             color=GHOST, fontsize=12.5)
    fig.text(0.945, 0.035, "python research api · rust engine", color=GHOST,
             fontsize=12.5, ha="right")

    fig.savefig(path, facecolor=BG)
    plt.close(fig)


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/nh_frames2"
    os.makedirs(out, exist_ok=True)
    d = load()
    total = 360
    for f in range(total):
        build_frame(d, f / (total - 1), os.path.join(out, f"f_{f:04d}.png"))
        if f % 90 == 0:
            print(f"{f}/{total}", flush=True)
    print(f"wrote {total} frames to {out}")


if __name__ == "__main__":
    main()
