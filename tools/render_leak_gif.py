"""Render the leak animation: two acts, with context and consequence.

    python3.10 tools/render_leak_gif.py [FRAMES_DIR]

420 frames, 1600x900, 30 fps, 14 s.

act 1 (0-5s), zoomed on the decision:
    what each strategy knew about SYM2.revenue, day by day.  the honest
    read is nothing until the filing (FEB 09: 253.4), then drops to 177.4
    the day the world finds out (MAR 01).  the naive read is 177.4 flat
    from JAN 02: today's terminal number, stamped onto every date.  the
    gate line sits at $200m.  a crimson counter ticks the days the naive
    read was early.

act 2 (5-14s), the whole backtest:
    the same window now a small band on the far left, both equity curves
    drawing over 750 days, and the gap at the end shaded and priced:
    +$21,891 made on information that did not exist yet.  verdict chips
    land.  crossfade between acts.

Stdlib + matplotlib.  Deterministic.
"""

from __future__ import annotations

import json
import os
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.image as mimage
import matplotlib.pyplot as plt
import numpy as np

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

FPS = 30
TOTAL = 420
ACT1_END = 150          # frame index where the crossfade starts
CROSSFADE = 15          # frames of blend


def ease(x):
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) ** 3


def load():
    with open(DATA) as f:
        return json.load(f)


def style_axes(ax):
    ax.set_facecolor(BG)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(HAIR)
    ax.tick_params(colors=HAIR, length=4)
    ax.grid(axis="y", color=HAIR, lw=0.7, alpha=0.6)


def header_footer(fig, act_label):
    fig.text(0.055, 0.945, "no", color=LAVENDER, fontsize=24, weight="bold")
    fig.text(0.076, 0.945, "hindsight", color=TEXT, fontsize=24, weight="bold")
    fig.text(0.148, 0.952, act_label, color=MUTED, fontsize=14.5)
    fig.text(0.055, 0.030,
             "43 decision days priced off a revision first published 2024-03-01 · up to 58 days early",
             color=GHOST, fontsize=12)
    fig.text(0.945, 0.030, "python research api · rust engine", color=GHOST,
             fontsize=12, ha="right")


def draw_act1(ax, d, t):
    """What each strategy knew.  x in day-offsets around the window."""
    dates = [p["date"] for p in d["curve"]]
    i0 = next(i for i, dt in enumerate(dates) if dt >= "2024-01-02")
    i1 = next(i for i, dt in enumerate(dates) if dt >= "2024-03-01")
    ifil = next(i for i, dt in enumerate(dates) if dt >= "2024-02-09")

    ax.set_xlim(i0 - 12, i1 + 14)
    ax.set_ylim(150, 290)
    ax.set_yticks([150, 200, 253.4, 177.4])
    ax.set_yticklabels(["$150m", "$200m gate", "filed 253.4", "restated 177.4"],
                       fontsize=12.5, color=MUTED)

    # the naive read: flat at 177.4 from the first decision day
    fn = ease(min(1.0, t / 0.30))
    xe = i0 + (i1 + 10 - i0) * fn
    ax.plot([i0, xe], [177.4, 177.4], color=CRIMSON, lw=6, alpha=0.12, solid_capstyle="round")
    ax.plot([i0, xe], [177.4, 177.4], color=CRIMSON, lw=1.8)
    if fn > 0.2:
        ax.text(i0 + 1, 168, "what the naive strategy read, every date from JAN 02",
                color=CRIMSON, fontsize=12.5)

    # the honest read: nothing until the filing, 253.4 until the restatement
    fh = ease(min(1.0, max(0.0, (t - 0.18) / 0.25)))
    xh_end = ifil + (i1 - ifil) * fh
    if fh > 0:
        ax.plot([ifil, xh_end], [253.4, 253.4], color=LAVENDER, lw=6, alpha=0.12)
        ax.plot([ifil, xh_end], [253.4, 253.4], color=LAVENDER, lw=1.8)
        ax.text(ifil, 262, "what the world had after the filing (FEB 09)",
                color=LAVENDER, fontsize=12.5)

    # the restatement: the step down, when t passes its date
    if fh >= 0.98 and t > 0.55:
        fs = ease(min(1.0, (t - 0.55) / 0.12))
        xs_step = [i1, i1 + (i1 + 10 - i1) * fs]
        ax.plot([i1, i1], [253.4, 177.4], color=SULFUR, lw=1.2, ls=(0, (2, 3)), alpha=0.8)
        ax.plot(xs_step, [177.4, 177.4], color=LAVENDER, lw=1.8)
        ax.plot([i1], [177.4], marker="o", color=SULFUR, ms=7)
        ax.text(i1 + 1, 182, "MAR 01: the world finds out. the number moved.",
                color=SULFUR, fontsize=12.5)

    # the gap between the reads, while it lasted
    if t > 0.45:
        g_end = min(xe, i1)
        if g_end > i0 + 1:
            ax.fill_between([i0, g_end], 177.4, 253.4, color=CRIMSON, alpha=0.07, lw=0)
        days = int(round(43 * ease(min(1.0, (t - 0.45) / 0.35))))
        ax.text(0.985, 0.06, f"decision days priced early: {days:2d} / 43",
                transform=ax.transAxes, color=CRIMSON if days < 43 else MUTED,
                fontsize=15, ha="right", weight="bold")

    ax.text(i0 - 10, 283, "the gate: hold SYM2 while the known revenue is at least $200m",
            color=MUTED, fontsize=12.5)


def draw_act2(ax, d, t):
    """The whole backtest, the gap priced."""
    n = len(d["curve"])
    dates = [p["date"] for p in d["curve"]]
    honest = [p["honest"] for p in d["curve"]]
    naive = [p["naive"] for p in d["curve"]]
    i0 = next(i for i, dt in enumerate(dates) if dt >= "2024-01-02")
    i1 = next(i for i, dt in enumerate(dates) if dt >= "2024-03-01")
    xs = list(range(n))

    ax.set_xlim(0, n - 1)
    ax.set_ylim(96_000, 150_000)
    ax.set_yticks([100_000, 120_000, 140_000])
    ax.set_yticklabels(["$100k", "$120k", "$140k"], fontsize=12.5, color=MUTED)
    ticks = list(range(0, n, 22))
    if ticks[-1] != n - 1:
        ticks.append(n - 1)
    ax.set_xticks(ticks)
    ax.set_xticklabels([dates[i][:7] for i in ticks], fontsize=11.5, color=MUTED)

    # the leak window, small now, with a pointer back to act 1
    ax.axvspan(i0, i1, color=CRIMSON, alpha=0.10, lw=0)
    ax.text((i0 + i1) / 2, 147_500, "the leak\nwindow", color=CRIMSON,
            fontsize=11, ha="center", va="top")

    fn = ease(min(1.0, t / 0.45))
    m = max(2, int(n * fn))
    ax.plot(xs[:m], honest[:m], color=LAVENDER, lw=6, alpha=0.10, solid_capstyle="round")
    ax.plot(xs[:m], honest[:m], color=LAVENDER, lw=1.6, solid_capstyle="round")
    ax.plot(xs[:m], naive[:m], color=CRIMSON, lw=6, alpha=0.10, solid_capstyle="round")
    ax.plot(xs[:m], naive[:m], color=CRIMSON, lw=1.6, solid_capstyle="round")
    if m > 4:
        ax.text(m - 1, honest[m - 1] - 5200, "honest", color=LAVENDER, fontsize=12.5, ha="right")
        ax.text(m - 1, naive[m - 1] + 3200, "naive", color=CRIMSON, fontsize=12.5, ha="right")

    # the gap after the window: shaded and priced
    if t > 0.50 and m > i1 + 2:
        fg = ease(min(1.0, (t - 0.50) / 0.18))
        ax.fill_between(xs[i1:m], honest[i1:m], naive[i1:m],
                        where=[nv > hh for nv, hh in zip(naive[i1:m], honest[i1:m])],
                        color=CRIMSON, alpha=0.09 * fg, lw=0, interpolate=True)
        if fg > 0.75:
            ax.annotate("+$21,891 made on a number\nthat did not exist yet",
                        xy=(n - 6, (naive[-1] + honest[-1]) / 2),
                        xytext=(n * 0.55, 104_000),
                        color=TEXT, fontsize=14,
                        arrowprops=dict(arrowstyle="->", color=CRIMSON, lw=1.4))

    if t > 0.78:
        ax.text(0.985, 0.965, "final: naive $143,764  ·  honest $121,873",
                transform=ax.transAxes, color=TEXT, fontsize=15, ha="right",
                va="top", weight="bold",
                bbox=dict(boxstyle="round,pad=0.5", fc="#12141C", ec=HAIR, lw=1.2))
    if t > 0.86:
        ax.text(0.985, 0.055, "naive · REAL · sharpe 1.17", transform=ax.transAxes,
                color=CRIMSON, fontsize=14, ha="right", weight="bold",
                bbox=dict(boxstyle="round,pad=0.4", fc="#12141C", ec=CRIMSON, lw=1.3))
        ax.text(0.985, 0.125, "honest · SUSPICIOUS · sharpe 0.69", transform=ax.transAxes,
                color=LAVENDER, fontsize=14, ha="right", weight="bold",
                bbox=dict(boxstyle="round,pad=0.4", fc="#12141C", ec=LAVENDER, lw=1.3))


def render_scene(d, t, act):
    fig = plt.figure(figsize=(16, 9), dpi=100, facecolor=BG)
    ax = fig.add_axes([0.075, 0.12, 0.855, 0.76])
    style_axes(ax)
    if act == 1:
        draw_act1(ax, d, t)
        header_footer(fig, "act 1 · what each strategy knew")
    else:
        draw_act2(ax, d, t)
        header_footer(fig, "act 2 · what it was worth")
    fig.canvas.draw()
    buf = np.asarray(fig.canvas.buffer_rgba())[:, :, :3].copy()
    plt.close(fig)
    return buf


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "/tmp/nh_frames2"
    os.makedirs(out, exist_ok=True)
    d = load()
    for f in range(TOTAL):
        t = f / (TOTAL - 1)
        if f < ACT1_END:
            buf = render_scene(d, f / (ACT1_END - 1), 1)
        elif f < ACT1_END + CROSSFADE:
            a = render_scene(d, 1.0, 1)
            b = render_scene(d, (f - ACT1_END) / (TOTAL - ACT1_END - CROSSFADE - 1), 2)
            k = (f - ACT1_END) / CROSSFADE
            buf = (a * (1 - k) + b * k).astype("uint8")
        else:
            buf = render_scene(d, (f - ACT1_END - CROSSFADE) / (TOTAL - ACT1_END - CROSSFADE - 1), 2)
        mimage.imsave(os.path.join(out, f"f_{f:04d}.png"), buf)
        if f % 90 == 0:
            print(f"{f}/{TOTAL}", flush=True)
    print(f"wrote {TOTAL} frames to {out}")


if __name__ == "__main__":
    main()
