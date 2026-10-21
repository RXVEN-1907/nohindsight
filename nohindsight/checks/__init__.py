"""nohindsight.checks — the police department.

Scanners that answer, about a bar series or a strategy's source: is
this dirty, and did anyone peek?  Every scanner returns Findings;
`run_all` bundles them into one CheckReport; `summarize` renders it for
the CLI.  Severity is the claim: LEAK invalidates the backtest, WARN
must be explained before trusting anything, INFO is color commentary.
"""

from .base import CheckReport, Finding, Severity
from .lookahead import LeakTrap, assert_no_future_access, scan_source
from .quality import (
    check_crossed,
    check_duplicates,
    check_gaps,
    check_ohlc,
    check_scale_shock,
    check_stale,
)
from .registry import run_all, summarize
from .splits import detect_splits

__all__ = [
    "Severity",
    "Finding",
    "CheckReport",
    "check_ohlc",
    "check_duplicates",
    "check_gaps",
    "check_stale",
    "check_crossed",
    "check_scale_shock",
    "detect_splits",
    "scan_source",
    "assert_no_future_access",
    "LeakTrap",
    "run_all",
    "summarize",
]
