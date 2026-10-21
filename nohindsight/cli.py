"""nohindsight.cli — python -m nohindsight.

Four doors into the library:

    version               print the version
    check <csv>           ingest a bar CSV, run the data police, write a report
    audit <returns_csv>   interrogate one return stream (or --demo one)
    demo [--out DIR]      the full story: planted crimes, two strategies,
                          one leak, two verdicts

``main(argv) -> int`` is the whole contract: 0 means the command ran and
found nothing disqualifying, 1 means it ran and found leaks (``check``),
2 means the input itself was unusable.  Argparse's own errors exit 2.
All printing lives here and in demo/ — the library stays silent.
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
from dataclasses import replace
from pathlib import Path

from . import __version__
from .checks import CheckReport, Severity, run_all, summarize
from .data.tabular import ingest_csv
from .forensics import audit
from .report import render_html

__all__ = ["main"]

_EXIT_OK = 0
_EXIT_LEAKS = 1
_EXIT_BAD_INPUT = 2

_RETURNS_COLUMN_CANDIDATES = ("return", "returns", "ret", "r", "value")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nohindsight",
        description="is your strategy real, or did you cheat by accident?",
    )
    parser.add_argument("--version", action="version", version=f"nohindsight {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_version = sub.add_parser("version", help="print the version")
    p_version.set_defaults(func=_cmd_version)

    p_check = sub.add_parser("check", help="run the data police over a bar CSV")
    p_check.add_argument("csv", help="CSV with key/event/knowledge columns plus OHLCV")
    p_check.add_argument("--out", default=None, help="report path (default: next to the CSV)")
    p_check.add_argument("--key-col", default="symbol")
    p_check.add_argument("--event-col", default="ts")
    p_check.add_argument("--knowledge-col", default="ts",
                         help="bars are knowable at their own close by default")
    p_check.set_defaults(func=_cmd_check)

    p_audit = sub.add_parser("audit", help="interrogate a one-column return CSV")
    p_audit.add_argument("csv", nargs="?", default=None,
                         help="one column of per-period returns")
    p_audit.add_argument("--demo", action="store_true",
                         help="audit a synthetic drift-y stream instead of a file")
    p_audit.add_argument("--n-trials", type=int, default=1,
                         help="how many configurations were tried (deflation hinge)")
    p_audit.add_argument("--ppy", type=int, default=252, help="periods per year")
    p_audit.add_argument("--out", default=None, help="report path (default: next to the CSV)")
    p_audit.set_defaults(func=_cmd_audit)

    p_demo = sub.add_parser("demo", help="the full story, end to end")
    p_demo.add_argument("--out", default=".", help="directory for report.html")
    p_demo.set_defaults(func=_cmd_demo)
    return parser


# -- version --------------------------------------------------------------


def _cmd_version(args: argparse.Namespace) -> int:
    print(f"nohindsight {__version__}")
    return _EXIT_OK


# -- check ----------------------------------------------------------------


def _bars_for_key(ledger, key: str) -> list[dict]:
    """Bars (ts + value dict) for one ledger key, event order."""
    entries = sorted(ledger.history(key), key=lambda e: e.event_time)
    first = entries[0].value if entries else None
    if not isinstance(first, dict):
        raise ValueError(
            f"{key!r}: check expects bar rows (open/high/low/close columns); "
            f"got a {type(first).__name__} value — ingest the OHLC columns "
            "without value_col"
        )
    return [{"ts": e.event_time.isoformat(), **e.value} for e in entries]


def _cmd_check(args: argparse.Namespace) -> int:
    csv_path = Path(args.csv)
    try:
        ledger = ingest_csv(
            str(csv_path),
            args.key_col,
            args.event_col,
            args.knowledge_col,
            name=csv_path.stem or "ingested",
        )
    except (OSError, ValueError) as exc:
        print(f"error: {exc}")
        return _EXIT_BAD_INPUT

    report = CheckReport()
    for key in sorted(ledger.keys()):
        try:
            bars = _bars_for_key(ledger, key)
        except ValueError as exc:
            print(f"error: {exc}")
            return _EXIT_BAD_INPUT
        for finding in run_all(bars).findings:
            report.add(
                replace(finding, where=f"{key} · {finding.where}" if finding.where else key)
            )

    print(summarize(report))
    counts = report.counts()
    print(
        f"scanned {len(ledger.keys())} symbol(s): "
        f"{counts[Severity.LEAK]} leaks, {counts[Severity.WARN]} warnings, "
        f"{counts[Severity.INFO]} info"
    )
    out = args.out or str(csv_path.with_name(csv_path.stem + ".report.html"))
    render_html(report, None, None, title=f"nohindsight check — {csv_path.name}", out_path=out)
    print(f"report: {out}")
    return _EXIT_LEAKS if report.has_leaks else _EXIT_OK


# -- audit ----------------------------------------------------------------


def _read_returns_csv(path: str) -> tuple[list[float], str]:
    """One column of per-period returns; headerless CSVs just work."""
    with open(path, "r", encoding="utf-8", newline="") as fh:
        rows = [r for r in csv.reader(fh) if r and any(c.strip() for c in r)]
    if not rows:
        raise ValueError(f"{path}: no rows")
    try:
        float(rows[0][0])
        start, col = 0, 0
    except ValueError:
        header = [h.strip().lower() for h in rows[0]]
        start = 1
        col = next(
            (header.index(c) for c in _RETURNS_COLUMN_CANDIDATES if c in header), -1
        )
        if col < 0:
            if len(header) == 1:
                col = 0
            else:
                raise ValueError(
                    f"{path}: {len(header)} columns, none named "
                    f"{_RETURNS_COLUMN_CANDIDATES} — pass a one-column CSV"
                ) from None
    returns: list[float] = []
    for lineno, row in enumerate(rows[start:], start=start + 1):
        text = row[col].strip() if col < len(row) else ""
        if not text:
            continue
        try:
            returns.append(float(text))
        except ValueError:
            raise ValueError(f"{path}: line {lineno}: {text!r} is not a number") from None
    if len(returns) < 2:
        raise ValueError(f"{path}: need at least 2 returns, got {len(returns)}")
    return returns, str(path)


def _demo_returns(seed: int = 11, n: int = 500) -> list[float]:
    """A synthetic stream with a real drift: mean 0.15%/day, sd 1%/day —
    a Sharpe around 2.4 that honest forensics should clear."""
    rng = random.Random(seed)
    return [0.0015 + rng.gauss(0.0, 0.01) for _ in range(n)]


def _cmd_audit(args: argparse.Namespace) -> int:
    if args.demo:
        returns = _demo_returns()
        source = "synthetic demo stream (seed 11)"
        default_out = "nohindsight-audit-demo.html"
    else:
        if not args.csv:
            print("error: audit needs a returns CSV (or --demo)")
            return _EXIT_BAD_INPUT
        try:
            returns, source = _read_returns_csv(args.csv)
        except (OSError, ValueError) as exc:
            print(f"error: {exc}")
            return _EXIT_BAD_INPUT
        csv_path = Path(args.csv)
        default_out = str(csv_path.with_name(csv_path.stem + ".report.html"))

    verdict = audit(returns, n_trials=args.n_trials, ppy=args.ppy)
    numbers = verdict.numbers
    bar = "=" * 64
    print(bar)
    print(f" nohindsight audit — {source}")
    print(bar)
    print(f" VERDICT: {verdict.level}")
    print(
        f" sharpe {numbers['sharpe']:.2f} · dsr_prob {numbers['dsr_prob']:.3f} · "
        f"p {numbers['p_value']:.3f} · max_dd {numbers['max_drawdown']:.1%}"
        + (f" · pbo {numbers['pbo']:.3f}" if numbers["pbo"] is not None else "")
    )
    print(" reasons:")
    for reason in verdict.reasons:
        print(f"  - {reason}")
    print(bar)

    out = args.out or default_out
    render_html(None, verdict, None, title=f"nohindsight audit — {source}", out_path=out)
    print(f"report: {out}")
    return _EXIT_OK


# -- demo -----------------------------------------------------------------


def _cmd_demo(args: argparse.Namespace) -> int:
    from . import demo  # imported here so cli stays cheap to import

    demo.run(args.out)
    return _EXIT_OK


# -- entry point -----------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Run one subcommand; return the process exit code."""
    parser = _build_parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    return int(args.func(args))
