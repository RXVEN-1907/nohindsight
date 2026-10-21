"""nohindsight.checks.registry — one call to ask everything.

`run_all` is the front door the CLI and the demo use: hand it bars, get
back one CheckReport.  The checks are independent witnesses — one ugly
overnight bar can legitimately draw both a SPLIT_SUSPECTED and a
SCALE_SHOCK; nobody dedupes testimony, the report weights it.

`summarize` renders a report as the aligned table the CLI prints.
"""

from __future__ import annotations

from .base import CheckReport, Finding, Severity
from .lookahead import scan_source
from .quality import (
    check_crossed,
    check_duplicates,
    check_gaps,
    check_ohlc,
    check_scale_shock,
    check_stale,
)
from .splits import detect_splits

__all__ = ["run_all", "summarize"]

_QUALITY_CHECKS = (
    check_ohlc,
    check_duplicates,
    check_gaps,
    check_stale,
    check_crossed,
    check_scale_shock,
)


def run_all(bars: list[dict], *, with_lookahead_scan: str | None = None) -> CheckReport:
    """Run every quality check plus the split detector over `bars`.

    with_lookahead_scan: optional strategy source text; when given, its
    scan_source findings ride along in the same report, so one report
    answers both "is the data dirty" and "does the code peek".
    """
    report = CheckReport()
    for check in _QUALITY_CHECKS:
        for finding in check(bars):
            report.add(finding)
    for finding in detect_splits(bars):
        report.add(finding)
    if with_lookahead_scan is not None:
        for finding in scan_source(with_lookahead_scan):
            report.add(finding)
    return report


def summarize(report: CheckReport) -> str:
    """Aligned text table: one row per (severity, code) group with its
    count, worst severity first.  An empty report renders as
    "no findings" — also a verdict, and the good one."""
    if not report.findings:
        return "no findings"
    groups: dict[tuple[Severity, str], int] = {}
    for finding in report.findings:
        key = (finding.severity, finding.code)
        groups[key] = groups.get(key, 0) + 1
    width = max([len("CODE")] + [len(code) for (_, code) in groups])
    lines = [f"{'CODE':<{width}}  {'SEVERITY':<8}  COUNT"]
    for (sev, code), count in sorted(groups.items(), key=lambda kv: (-kv[0][0], kv[0][1])):
        lines.append(f"{code:<{width}}  {sev.name:<8}  {count:>5}")
    return "\n".join(lines)
