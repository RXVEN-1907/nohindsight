"""nohindsight.report — one self-contained HTML file per interrogation.

The design contract (docs/SPEC.md module 6):

    canvas      #0B0C10  obsidian ink          text #F2F3F7  clean off-white
    truth       #D0C4DF  lavender — the honest curve, the baseline, REAL verdicts
    leaks       #E5484D  crimson  — LEAK findings, FANTASY verdicts, the leak curve
    warnings    #E6B455  sulfur   — WARN findings, SUSPICIOUS verdicts
    info        #5B8DEF  blue     — INFO findings

Everything is inline: no fonts, no scripts, no images fetched.  The report
is a single file that can be archived next to the ledger it describes and
will render identically in ten years.  The equity sparkline is a hand-built
SVG path — line segments, not a chart library — because a report about
honesty should contain no machinery nobody can audit.

Beyond the five spec parameters, ``render_html`` takes keyword-only extras
used by the demo (a second equity line, further verdicts, postscript
notes); every spec-shaped call works unchanged.
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any, Callable, Optional, Sequence

from . import __version__
from .checks.base import CheckReport, Finding, Severity
from .forensics.audit import Verdict

__all__ = ["render_html", "PALETTE"]

# The palette, as named constants — reports and tests may import these
# instead of hard-coding hex triplets.
PALETTE = {
    "bg": "#0B0C10",
    "text": "#F2F3F7",
    "lavender": "#D0C4DF",
    "crimson": "#E5484D",
    "sulfur": "#E6B455",
    "info": "#5B8DEF",
    "muted": "#9BA0B0",
    "card": "#12141B",
    "border": "#232634",
    "grid": "#3A3D4A",
}

_LEVEL_COLORS: dict[str, str] = {
    "REAL": PALETTE["lavender"],
    "SUSPICIOUS": PALETTE["sulfur"],
    "FANTASY": PALETTE["crimson"],
}

_SEVERITY_COLORS: dict[Severity, str] = {
    Severity.LEAK: PALETTE["crimson"],
    Severity.WARN: PALETTE["sulfur"],
    Severity.INFO: PALETTE["info"],
}

_SEVERITY_WORDS: dict[Severity, str] = {
    Severity.LEAK: "leaks",
    Severity.WARN: "warnings",
    Severity.INFO: "info",
}

_MAX_FINDING_ROWS = 500

_CSS = """
:root {
  --bg: __BG__; --text: __TEXT__; --muted: __MUTED__;
  --card: __CARD__; --border: __BORDER__; --grid: __GRID__;
  --lavender: __LAVENDER__; --crimson: __CRIMSON__;
  --sulfur: __SULFUR__; --info: __INFO__;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
    "Helvetica Neue", Arial, sans-serif;
  font-size: 16px; line-height: 1.65;
}
main { max-width: 860px; margin: 0 auto; padding: 48px 24px 64px; }
.brand {
  color: var(--lavender); letter-spacing: 0.18em; text-transform: uppercase;
  font-size: 12px; font-weight: 600;
}
h1 { font-size: 30px; line-height: 1.25; margin: 6px 0 4px; font-weight: 650; }
.tagline { color: var(--muted); margin: 0 0 30px; font-size: 14px; }
.card {
  background: var(--card); border: 1px solid var(--border);
  border-left: 4px solid var(--lavender); border-radius: 10px;
  padding: 20px 22px; margin: 0 0 18px;
}
.vlabel {
  font-size: 12px; letter-spacing: 0.14em; text-transform: uppercase;
  color: var(--muted); margin: 0 0 6px;
}
.level { font-size: 22px; font-weight: 700; letter-spacing: 0.04em; }
.stats { display: flex; flex-wrap: wrap; gap: 10px 28px; margin: 14px 0 4px; }
.stat .k {
  font-size: 11px; letter-spacing: 0.1em; text-transform: uppercase;
  color: var(--muted);
}
.stat .v { font-size: 17px; font-variant-numeric: tabular-nums; }
.card ul { margin: 12px 0 0; padding-left: 20px; }
.card li { margin: 6px 0; }
section { margin: 36px 0 0; }
h2 {
  font-size: 15px; text-transform: uppercase; letter-spacing: 0.14em;
  color: var(--muted); font-weight: 600; margin: 0 0 12px;
}
.strip { display: flex; flex-wrap: wrap; gap: 18px; font-size: 13px; margin: 0 0 12px; }
.dot {
  display: inline-block; width: 9px; height: 9px; border-radius: 50%;
  margin-right: 6px;
}
table { width: 100%; border-collapse: collapse; font-size: 13.5px; }
th {
  text-align: left; color: var(--muted); font-size: 11px;
  letter-spacing: 0.1em; text-transform: uppercase; padding: 8px 10px;
  border-bottom: 1px solid var(--border);
}
td { padding: 9px 10px; border-bottom: 1px solid var(--border); vertical-align: top; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 12.5px; }
td.where { color: var(--muted); white-space: nowrap; }
.empty { color: var(--muted); font-style: italic; }
svg { width: 100%; height: auto; display: block; }
.legend { display: flex; flex-wrap: wrap; gap: 20px; font-size: 13px; margin-top: 10px; }
.legend .swatch { color: var(--muted); }
.note {
  background: var(--card); border: 1px solid var(--border); border-radius: 10px;
  padding: 16px 20px; margin: 28px 0 0;
}
.note p { margin: 8px 0; }
.closing { color: var(--lavender); font-size: 17px; }
footer {
  margin-top: 46px; color: var(--muted); font-size: 12.5px;
  border-top: 1px solid var(--border); padding-top: 16px;
}
""".replace("__BG__", PALETTE["bg"]).replace("__TEXT__", PALETTE["text"]) \
    .replace("__MUTED__", PALETTE["muted"]).replace("__CARD__", PALETTE["card"]) \
    .replace("__BORDER__", PALETTE["border"]).replace("__GRID__", PALETTE["grid"]) \
    .replace("__LAVENDER__", PALETTE["lavender"]).replace("__CRIMSON__", PALETTE["crimson"]) \
    .replace("__SULFUR__", PALETTE["sulfur"]).replace("__INFO__", PALETTE["info"])


def _esc(value: Any) -> str:
    return html.escape(str(value))


def _ts_label(ts: Any) -> str:
    """A short human date from a datetime or an ISO-ish string."""
    if isinstance(ts, datetime):
        return ts.date().isoformat()
    text = str(ts)
    return text[:10] if len(text) >= 10 else text


def _num(value: Any, spec: str = "{:,.2f}") -> str:
    if value is None:
        return "&mdash;"
    try:
        return spec.format(float(value))
    except (TypeError, ValueError):
        return _esc(value)


# -- verdict cards -------------------------------------------------------


_STAT_SPECS: tuple[tuple[str, str, Callable[[Any], str]], ...] = (
    ("sharpe", "Sharpe", lambda v: "&mdash;" if v is None else f"{float(v):.2f}"),
    ("dsr_prob", "DSR prob", lambda v: "&mdash;" if v is None else f"{float(v):.1%}"),
    ("p_value", "perm p", lambda v: "&mdash;" if v is None else f"{float(v):.3f}"),
    ("max_drawdown", "max DD", lambda v: "&mdash;" if v is None else f"{float(v):.1%}"),
    ("pbo", "PBO", lambda v: "not run" if v is None else f"{float(v):.1%}"),
    ("n_obs", "obs", lambda v: "&mdash;" if v is None else str(int(v))),
)


def _verdict_card(label: str, verdict: Verdict) -> str:
    color = _LEVEL_COLORS.get(verdict.level, PALETTE["lavender"])
    stats: list[str] = []
    for key, name, fmt in _STAT_SPECS:
        value = verdict.numbers.get(key)
        stats.append(
            f'<div class="stat"><div class="k">{_esc(name)}</div>'
            f'<div class="v">{fmt(value)}</div></div>'
        )
    reasons = "".join(f"<li>{_esc(r)}</li>" for r in verdict.reasons)
    return (
        f'<div class="card" style="border-left-color:{color}">'
        f'<p class="vlabel">{_esc(label)}</p>'
        f'<div class="level" style="color:{color}">{_esc(verdict.level)}</div>'
        f'<div class="stats">{"".join(stats)}</div>'
        f"<ul>{reasons}</ul>"
        "</div>"
    )


# -- findings ------------------------------------------------------------


def _finding_row(f: Finding) -> str:
    color = _SEVERITY_COLORS[f.severity]
    return (
        "<tr>"
        f'<td><span class="dot" style="background:{color}"></span>{f.severity.name}</td>'
        f'<td class="mono">{_esc(f.code)}</td>'
        f'<td class="where mono">{_esc(f.where)}</td>'
        f'<td class="msg">{_esc(f.message)}</td>'
        "</tr>"
    )


def _findings_section(report: Optional[CheckReport]) -> str:
    if report is None:
        return ""
    counts = report.counts()
    strip = " &middot; ".join(
        f"{counts[sev]} {_SEVERITY_WORDS[sev]}" for sev in (Severity.LEAK, Severity.WARN, Severity.INFO)
    )
    rows = sorted(report.findings, key=lambda f: (-int(f.severity), f.code, f.where))
    shown = rows[:_MAX_FINDING_ROWS]
    body = "".join(_finding_row(f) for f in shown)
    more = ""
    if len(rows) > _MAX_FINDING_ROWS:
        more = (
            f'<p class="empty">&hellip;and {len(rows) - _MAX_FINDING_ROWS} more findings '
            "not shown.</p>"
        )
    if not rows:
        body = '<tr><td colspan="4" class="empty">no findings &mdash; the good verdict</td></tr>'
    return (
        "<section><h2>Findings</h2>"
        f'<div class="strip">{strip}</div>'
        "<table><thead><tr><th>Severity</th><th>Code</th><th>Where</th><th>What</th></tr></thead>"
        f"<tbody>{body}</tbody></table>{more}</section>"
    )


# -- the equity sparkline -------------------------------------------------


def _series_path(points: Sequence[tuple[Any, float]], lo: float, hi: float,
                 x_left: float, x_right: float, y_top: float, y_bottom: float) -> tuple[str, tuple[float, float]]:
    """SVG path 'M x,y L x,y ...' for one series; returns (path, (last_x, last_y))."""
    n = len(points)
    span = hi - lo
    vspan = y_bottom - y_top
    hspan = x_right - x_left
    cmds: list[str] = []
    last = (0.0, 0.0)
    for i, (_, value) in enumerate(points):
        x = x_left + (i / (n - 1)) * hspan
        y = y_top + (1.0 - (value - lo) / span) * vspan
        cmds.append(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}")
        last = (x, y)
    return " ".join(cmds), (last[0], last[1])


def _sparkline_svg(series: list[dict]) -> str:
    """Hand-built SVG: gridlines, a dashed start baseline, one path per
    series, end labels nudged apart if they collide."""
    usable = [s for s in series if len(s["points"]) >= 2]
    if not usable:
        return ""
    values = [v for s in usable for _, v in s["points"]]
    lo, hi = min(values), max(values)
    if hi - lo < 1e-12:
        lo, hi = lo - 1.0, hi + 1.0
    pad = (hi - lo) * 0.06
    lo, hi = lo - pad, hi + pad

    W, H = 800.0, 240.0
    x_left, y_top, y_bottom = 10.0, 16.0, 200.0  # below y_bottom: the date labels
    plot_right = 640.0  # the label gutter (end labels) starts here

    def X(frac: float) -> float:
        return x_left + frac * (plot_right - x_left)

    def Y(v: float) -> float:
        return y_top + (1.0 - (v - lo) / (hi - lo)) * (y_bottom - y_top)

    parts = [
        f'<svg viewBox="0 0 {W:.0f} {H:.0f}" xmlns="http://www.w3.org/2000/svg" '
        'role="img" aria-label="equity curves">',
        f'<rect x="0" y="0" width="{W:.0f}" height="{H:.0f}" fill="{PALETTE["bg"]}"/>',
    ]
    # gridlines + value labels
    for frac, gv in ((0.0, hi), (0.5, (lo + hi) / 2.0), (1.0, lo)):
        gy = Y(gv)
        parts.append(
            f'<line x1="{x_left:.0f}" y1="{gy:.1f}" x2="{plot_right:.0f}" y2="{gy:.1f}" '
            f'stroke="{PALETTE["border"]}" stroke-width="1"/>'
        )
        parts.append(
            f'<text x="{x_left:.0f}" y="{gy - 4:.1f}" fill="{PALETTE["muted"]}" '
            f'font-size="11" font-family="ui-monospace, Menlo, monospace">'
            f"{gv:,.0f}</text>"
        )
    # the start baseline — where both accounts began
    start = usable[0]["points"][0][1]
    sy = Y(start)
    parts.append(
        f'<line x1="{x_left:.0f}" y1="{sy:.1f}" x2="{plot_right:.0f}" y2="{sy:.1f}" '
        f'stroke="{PALETTE["grid"]}" stroke-width="1" stroke-dasharray="4 4"/>'
    )
    # one path per series; the first also gets a faint area fill
    for idx, s in enumerate(usable):
        path, (lx, ly) = _series_path(
            s["points"], lo, hi, X(0.0), X(1.0), y_top, y_bottom
        )
        color = s["color"]
        if idx == 0:
            base = f"{sy:.1f}"
            parts.append(
                f'<path d="{path} L{lx:.1f},{base} L{X(0.0):.1f},{base} Z" '
                f'fill="{color}" opacity="0.07" stroke="none"/>'
            )
        parts.append(
            f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2" '
            'stroke-linejoin="round" stroke-linecap="round"/>'
        )
        s["_end"] = (lx, ly)
    # end labels, nudged apart when they would overlap
    ends = sorted(usable, key=lambda s: s["_end"][1])
    last_y = -1e9
    for s in ends:
        lx, ly = s["_end"]
        ly = max(ly, last_y + 15.0)
        last_y = ly
        text = f'{s["label"]}  {s["points"][-1][1]:,.0f}'
        parts.append(
            f'<text x="{lx + 8:.1f}" y="{ly + 4:.1f}" fill="{s["color"]}" '
            'font-size="12" font-family="ui-monospace, Menlo, monospace">'
            f"{_esc(text)}</text>"
        )
    # time labels under the chart, from the primary series
    primary = usable[0]["points"]
    parts.append(
        f'<text x="{x_left:.0f}" y="{H - 8:.0f}" fill="{PALETTE["muted"]}" font-size="11">'
        f"{_esc(_ts_label(primary[0][0]))}</text>"
    )
    parts.append(
        f'<text x="{plot_right:.0f}" y="{H - 8:.0f}" fill="{PALETTE["muted"]}" font-size="11" '
        f'text-anchor="end">{_esc(_ts_label(primary[-1][0]))}</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def _equity_section(series: list[dict]) -> str:
    if not series:
        return ""
    svg = _sparkline_svg(series)
    if not svg:
        return ""
    legend = "".join(
        f'<span class="swatch"><span class="dot" style="background:{s["color"]}"></span>'
        f'{_esc(s["label"])}</span>'
        for s in series
    )
    return (
        "<section><h2>Equity</h2>"
        f"{svg}"
        f'<div class="legend">{legend}</div>'
        "</section>"
    )


# -- the one public function ----------------------------------------------


def render_html(
    report: Optional[CheckReport],
    verdict: Optional[Verdict],
    equity: Optional[list[tuple[Any, float]]],
    title: str,
    out_path: str,
    *,
    leak_equity: Optional[list[tuple[Any, float]]] = None,
    extra_verdicts: Optional[list[Verdict]] = None,
    verdict_labels: Optional[list[str]] = None,
    notes: Optional[list[str]] = None,
) -> str:
    """Render one self-contained dark-theme HTML report to ``out_path``.

    Spec signature: report, verdict, equity, title, out_path — any of the
    first three may be None and its section is omitted.  Keyword-only extras
    (used by the demo): ``leak_equity`` draws a second equity line in
    crimson over the same chart, ``extra_verdicts`` render as further
    verdict cards, ``verdict_labels`` names the cards in order, and
    ``notes`` become a postscript section.  Returns the HTML written.
    """
    verdicts: list[tuple[str, Verdict]] = []
    if verdict is not None:
        verdicts.append((verdict_labels[0] if verdict_labels else "Verdict", verdict))
    for i, v in enumerate(extra_verdicts or []):
        idx = i + 1
        label = verdict_labels[idx] if verdict_labels and idx < len(verdict_labels) else "Verdict"
        verdicts.append((label, v))

    cards = "".join(_verdict_card(label, v) for label, v in verdicts)

    chart_series: list[dict] = []
    if equity:
        chart_series.append(
            {
                "label": verdict_labels[0] if verdict_labels else "Equity",
                "points": equity,
                "color": PALETTE["lavender"],
            }
        )
        if leak_equity:
            chart_series.append(
                {
                    "label": verdict_labels[1] if verdict_labels and len(verdict_labels) > 1 else "Leak",
                    "points": leak_equity,
                    "color": PALETTE["crimson"],
                }
            )

    note_html = ""
    if notes:
        body = "".join(
            f'<p class="closing">{_esc(n)}</p>' if i == 0 else f"<p>{_esc(n)}</p>"
            for i, n in enumerate(notes)
        )
        note_html = f'<div class="note">{body}</div>'

    doc = (
        "<!doctype html>"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_esc(title)}</title>"
        f"<style>{_CSS}</style>"
        "</head><body><main>"
        '<header><div class="brand">nohindsight</div>'
        f"<h1>{_esc(title)}</h1>"
        '<p class="tagline">is your strategy real, or did you cheat by accident?</p>'
        "</header>"
        f"{cards}"
        f"{_findings_section(report)}"
        f"{_equity_section(chart_series)}"
        f"{note_html}"
        f"<footer>generated by nohindsight {_esc(__version__)} &mdash; "
        "pure stdlib; nothing phoned home</footer>"
        "</main></body></html>"
    )
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(doc)
    return doc
