"""nohindsight.checks.base — the vocabulary of a complaint.

Every scanner in this package returns Findings; a CheckReport collects
them.  Severity is a claim about consequence, not politeness:

    LEAK  this invalidates the backtest — a number the world didn't have
          yet, or data too broken to price anything honestly
    WARN  suspicious but survivable; explain it before trusting anything
    INFO  worth knowing, rarely disqualifying
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

__all__ = ["Severity", "Finding", "CheckReport"]


class Severity(enum.IntEnum):
    """Ordered INFO < WARN < LEAK.

    The order is load-bearing: the summary table and the report layer
    sort by it, so the worst thing in a report surfaces first.
    """

    INFO = 0
    WARN = 1
    LEAK = 2


@dataclass(frozen=True)
class Finding:
    """One scanner's one complaint, fully located.

    `code` is a stable machine key (e.g. "SPLIT_SUSPECTED") that reports
    and tables group on.  `message` is the human sentence.  `where`
    points at the evidence in the input (a bar index, a source line) —
    a report should point, not gesture.  `detail` carries the numbers
    behind the message; keep the values JSON-friendly so findings can be
    persisted next to the ledgers they describe.
    """

    code: str
    severity: Severity
    message: str
    where: str = ""
    detail: dict = field(default_factory=dict)


class CheckReport:
    """A bag of findings from one or more scanners.

    Truthiness is the leak question, deliberately: `if report:` means
    "this backtest is invalid", not "something was said".  A report full
    of WARNs is falsy — worrying, but alive.
    """

    def __init__(self) -> None:
        self.findings: list[Finding] = []

    def add(self, finding: Finding) -> Finding:
        """Collect one finding; returns it so callers can pass through."""
        self.findings.append(finding)
        return finding

    def counts(self) -> dict[Severity, int]:
        """Finding count per severity, every severity present (0 included)."""
        out: dict[Severity, int] = {sev: 0 for sev in Severity}
        for finding in self.findings:
            out[finding.severity] += 1
        return out

    @property
    def has_leaks(self) -> bool:
        return any(f.severity == Severity.LEAK for f in self.findings)

    def __bool__(self) -> bool:
        return self.has_leaks
