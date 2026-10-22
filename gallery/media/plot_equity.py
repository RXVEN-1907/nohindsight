#!/usr/bin/env python3
"""One figure, two equity curves, one leak.

Reads gallery/data/leak_demo.json and draws the honest curve against the
naive one. Lavender is what a point-in-time backtest earns. Crimson is
what reading a future revision buys. The shaded band is the span where
the naive path knew too much.

Output: equity_curves.png, 1600x900, next to this script.

Run:
    python3 plot_equity.py            # renders the png
    python3 plot_equity.py --check    # loads the JSON, prints stats, draws nothing
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA_PATH = HERE.parent / "data" / "leak_demo.json"
OUT_PATH = HERE / "equity_curves.png"

# Tokens from docs/DESIGN.md. Lavender is the truth. Crimson is the leak,
# used only where the leak is, never as decoration.
CANVAS = "#0B0C10"
PANEL = "#12141C"
HAIRLINE = "#2A2D3A"
TEXT = "#F2F3F7"
MUTED = "#8A8FA3"
LAVENDER = "#D0C4DF"
CRIMSON = "#E5484D"


def load_demo(path: Path = DATA_PATH) -> dict:
    # The exporter writes bare NaN literals. That is not strict JSON.
    # Python's json module reads it anyway. Most other parsers will not.
    with path.open() as fh:
        return json.load(fh)


def summarize(data: dict) -> str:
    curve = data["curve"]
    d = data["leak"]["detail"]
    first, last = curve[0], curve[-1]
    win_start = date.fromisoformat(d["first_leak_day"])
    win_end = date.fromisoformat(d["knowledge_time"])
    lines = [
        f"points          : {len(curve)}",
        f"range           : {first['date']} .. {last['date']}",
        f"honest          : final {last['honest']:,.2f}, verdict {data['honest']['level']}, sharpe {data['honest']['sharpe']}",
        f"naive           : final {last['naive']:,.2f}, verdict {data['naive']['level']}, sharpe {data['naive']['sharpe']}",
        f"leak            : {data['leak']['code']}, severity {data['leak']['severity']}",
        f"leak days       : {d['leak_days']}, max lag {d['max_lag_days']} days",
        f"window          : {win_start} .. {win_end} ({(win_end - win_start).days} days)",
    ]
    return "\n".join(lines)


def render(data: dict, out_path: Path) -> None:
    # Agg before pyplot: this runs on boxes with no display.
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.dates as mdates
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter

    plt.rcParams.update({"font.family": "monospace"})

    curve = data["curve"]
    days = [date.fromisoformat(p["date"]) for p in curve]
    honest = [p["honest"] for p in curve]
    naive = [p["naive"] for p in curve]

    h, n = data["honest"], data["naive"]
    d = data["leak"]["detail"]
    # The band edges come from the receipt, not from eyeballing the curve.
    # first_leak_day: first decision that read the early revision.
    # knowledge_time: the day the revision went public, where the band ends.
    win_start = date.fromisoformat(d["first_leak_day"])
    win_end = date.fromisoformat(d["knowledge_time"])

    fig, ax = plt.subplots(figsize=(16, 9), dpi=100)
    fig.patch.set_facecolor(CANVAS)
    ax.set_facecolor(CANVAS)

    # The shaded span is the crime scene. Inside it, the naive backtest
    # reads SYM2.revenue from a snapshot that already held the
    # knowledge_time revision. Left of the band both clocks agree.
    # Inside it the gap between the curves is stolen.
    ax.axvspan(win_start, win_end, color=CRIMSON, alpha=0.08, lw=0, zorder=0)

    ax.plot(days, honest, color=LAVENDER, lw=2.0, label="honest (guarded)")
    ax.plot(days, naive, color=CRIMSON, lw=2.0, label="naive (leaking)")

    # Final values and verdict levels, past the last point. The numbers
    # alone do not incriminate. The levels do.
    pad = timedelta(days=150)
    ax.annotate(
        f"naive : {n['level']}\n{naive[-1]:,.0f}  Sharpe {n['sharpe']:.2f}",
        xy=(days[-1], naive[-1]),
        xytext=(8, 0),
        textcoords="offset points",
        color=CRIMSON,
        fontsize=11,
        va="center",
        ha="left",
    )
    ax.annotate(
        f"honest : {h['level']}\n{honest[-1]:,.0f}  Sharpe {h['sharpe']:.2f}",
        xy=(days[-1], honest[-1]),
        xytext=(8, 0),
        textcoords="offset points",
        color=LAVENDER,
        fontsize=11,
        va="center",
        ha="left",
    )

    # Vertical label inside the band. The band is narrow, so the text
    # runs down it: code first, dates in the subtitle.
    ax.text(
        win_start + (win_end - win_start) / 2,
        0.52,
        data["leak"]["code"],
        transform=ax.get_xaxis_transform(),
        color=CRIMSON,
        fontsize=8.5,
        alpha=0.9,
        rotation=90,
        ha="center",
        va="center",
    )

    fig.suptitle("same strategy, one leak", color=TEXT, fontsize=17, x=0.061, ha="left", y=0.968)
    ax.set_title(
        f"{d['key']} read on {d['leak_days']} decision days from a snapshot revised "
        f"{d['knowledge_time']} : max lag {d['max_lag_days']} days",
        color=MUTED,
        fontsize=10.5,
        loc="left",
        pad=14,
    )
    ax.set_ylabel("equity, USD", color=MUTED, fontsize=10)

    lo = min(honest + naive)
    hi = max(honest + naive)
    ax.set_ylim(lo * 0.985, hi * 1.015)
    ax.set_xlim(days[0], days[-1] + pad)

    ax.grid(axis="y", color=HAIRLINE, lw=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(HAIRLINE)
    ax.tick_params(colors=MUTED, labelsize=10)

    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _pos: f"{v:,.0f}"))
    loc = mdates.AutoDateLocator(minticks=6, maxticks=10)
    ax.xaxis.set_major_locator(loc)
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(loc))
    ax.xaxis.get_offset_text().set_color(MUTED)

    leg = ax.legend(
        loc="upper left",
        frameon=True,
        facecolor=PANEL,
        edgecolor=HAIRLINE,
        fontsize=11,
        labelcolor=TEXT,
    )
    leg.get_frame().set_linewidth(1.0)

    fig.savefig(out_path, dpi=100, facecolor=CANVAS)
    print(f"wrote {out_path} ({out_path.stat().st_size} bytes)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Render the demo leak as one equity chart.")
    ap.add_argument("--check", action="store_true", help="load the JSON, print summary stats, draw nothing")
    ap.add_argument("--out", type=Path, default=OUT_PATH, help="output png path")
    args = ap.parse_args(argv)

    data = load_demo()
    if args.check:
        print(summarize(data))
        return 0
    render(data, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
