"""nohindsight.checks.lookahead - static + runtime police for peeking code.

Two instruments:

  * `scan_source` - an AST lint over strategy source.  It flags the
    classic shapes of accidental lookahead: negative shifts, indexing
    from the end of history, future-flavored names used as subscripts.
    Every finding is a WARN and every message ends in "hint, not
    proof": code can smell like lookahead and be innocent; only the two
    clocks can judge.

  * `assert_no_future_access` / `LeakTrap` - a runtime probe: run the
    function under the real clock, then under a clock nudged forward,
    and demand identical answers.

The probe's fine print (documented assumption, load-bearing): between
now and now + `steps` days NOTHING new may become knowable in the
ledger (call it after a bar closes and before the next one exists),
and fn must be deterministic and stateless.  Under those assumptions an
honest function sees the same world twice and must answer the same
thing twice; a function whose answers differ is reading something
*relative to now* that only exists later - i.e., it peeked.  A function
that reads one FIXED future timestamp comes back identical at both
clocks and slips past this probe; that shape is scan_source's to catch.
A probe is a trap, not a proof.

One probe step is one day: daily bars are this repo's grain.
"""

from __future__ import annotations

import ast
import re
from datetime import timedelta
from typing import Any

from ..core import Ledger, LeakError
from .base import Finding, Severity

__all__ = ["scan_source", "assert_no_future_access", "LeakTrap"]

# R3's pattern: the vocabulary a leak writes into variable names.
_FUTURE_NAME = re.compile(r"future|tomorrow|next_day|t_plus|forward", re.IGNORECASE)

_RULE_HINT = "hint, not proof"


def _fn_name(fn: Any) -> str:
    return getattr(fn, "__name__", repr(fn))


def _is_negative_constant(node: ast.AST) -> bool:
    """True for a syntactically negative number.

    `-1` in source parses as UnaryOp(USub, Constant(1)) - Python folds
    the sign at compile time, not parse time - but generated code can
    carry Constant(-1) directly.  Handle both.
    """
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        operand = node.operand
        return isinstance(operand, ast.Constant) and isinstance(operand.value, (int, float))
    if isinstance(node, ast.Constant):
        value = node.value
        return isinstance(value, (int, float)) and value < 0
    return False


def _shift_reads_future(call: ast.Call) -> bool:
    """shift(-n) positionally, or shift(periods=-n) by keyword."""
    if call.args and _is_negative_constant(call.args[0]):
        return True
    return any(
        kw.arg == "periods" and _is_negative_constant(kw.value)
        for kw in call.keywords
    )


def _future_name_in(slice_node: ast.AST) -> str | None:
    """The first future-flavored name used inside a subscript/slice.

    String keys count too: df["next_day_high"] is the most common leak
    vehicle there is - a column that could only exist after the fact.
    """
    for node in ast.walk(slice_node):
        if isinstance(node, ast.Name) and _FUTURE_NAME.search(node.id):
            return node.id
        if isinstance(node, ast.Attribute) and _FUTURE_NAME.search(node.attr):
            return node.attr
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _FUTURE_NAME.search(node.value):
                return node.value
    return None


def scan_source(source: str) -> list[Finding]:
    """AST-lint strategy source for lookahead shapes.

    Rules (all WARN; each message explains the suspicion and ends in
    "hint, not proof"):
      R1  .shift(-n) - a negative shift reads the future
      R2  .iloc[-n] / .loc[...-n...] - reading from the end of history;
          if the frame reaches past the clock, that read is the future
      R3  a name matching future|tomorrow|next_day|t_plus|forward used
          as a subscript or slice
    (R4 - assignments compared across time - is deliberately not
    implemented; one expression can't say who moved first.)
    `source` must parse; SyntaxError propagates.
    """
    tree = ast.parse(source)
    findings: list[Finding] = []
    for node in ast.walk(tree):
        where = f"line {getattr(node, 'lineno', '?')}"
        snippet = ast.get_source_segment(source, node) or ""
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "shift":
            if _shift_reads_future(node):
                findings.append(Finding(
                    code="AST_NEG_SHIFT",
                    severity=Severity.WARN,
                    message=(
                        f"{snippet!r}: shift(-n) reads the future - a negative "
                        f"shift is 'look ahead n bars'; {_RULE_HINT}"
                    ),
                    where=where,
                    detail={"rule": "R1", "snippet": snippet},
                ))
        # R2 and R3 are independent witnesses: a .loc[...] subscript can
        # be guilty of both, and .loc[future_name] is guilty of R3 alone.
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute) \
                and node.value.attr in ("iloc", "loc"):
            if any(_is_negative_constant(n) for n in ast.walk(node.slice)):
                findings.append(Finding(
                    code="AST_TAIL_INDEX",
                    severity=Severity.WARN,
                    message=(
                        f"{snippet!r}: .iloc/.loc indexed from the end of history - "
                        f"if the frame reaches past the clock, that read is the "
                        f"future; {_RULE_HINT}"
                    ),
                    where=where,
                    detail={"rule": "R2", "snippet": snippet},
                ))
        if isinstance(node, ast.Subscript):
            name = _future_name_in(node.slice)
            if name is not None:
                findings.append(Finding(
                    code="AST_FUTURE_NAME",
                    severity=Severity.WARN,
                    message=(
                        f"{snippet!r}: name {name!r} looks future-flavored and is "
                        f"used as a subscript - variable names this honest are "
                        f"usually honest about a crime; {_RULE_HINT}"
                    ),
                    where=where,
                    detail={"rule": "R3", "snippet": snippet, "name": name},
                ))
    return findings


_Outcome = tuple[str, Any]  # ("value", result) or ("raised", repr(exc))


def _run_twice(
    fn: Any,
    ledger: Ledger,
    clock: Any,
    steps: int,
    args: tuple,
    kwargs: dict,
) -> tuple[_Outcome, _Outcome]:
    """Run fn(view, ...) under `clock`, then under a fork advanced
    `steps` days; return both outcomes.

    The first call's exception propagates (that's fn's business, not the
    probe's); only a difference in *raising* on the second run is signal.
    """
    first = fn(ledger.view(clock), *args, **kwargs)
    probe_clock = clock.fork()
    probe_clock.advance(timedelta(days=steps))
    try:
        second = fn(ledger.view(probe_clock), *args, **kwargs)
    except Exception as exc:  # a difference in raising IS the signal
        return ("value", first), ("raised", repr(exc))
    return ("value", first), ("value", second)


def assert_no_future_access(
    fn: Any,
    ledger: Ledger,
    clock: Any,
    steps: int = 1,
    *args: Any,
    **kwargs: Any,
) -> Any:
    """Run fn(view, *args, **kwargs) under `clock`, then under a probe
    clock advanced `steps` days; raise LeakError if the answers differ.

    Read the module docstring for the probe's assumptions (unchanged
    world inside the probe window, deterministic fn) and its blind spot
    (fixed-timestamp peeks look identical at both clocks - scan_source's
    territory).  Returns the first run's result on success.
    """
    first, second = _run_twice(fn, ledger, clock, steps, args, kwargs)
    if first != second:
        raise LeakError(
            f"{_fn_name(fn)} is not time-invariant in an unchanged world: "
            f"at {clock.now.isoformat()} it gave {first[1]!r}, one probe-step "
            f"({steps}d) later it gave {second[0]} {second[1]!r} - those must "
            "match, so fn reads beyond the clock (probe assumptions in the "
            "lookahead module docstring)",
            as_of=clock.now,
        )
    return first[1]


class LeakTrap:
    """Wraps fn(view, *a, **kw) and arms the runtime probe on every call.

    Unlike assert_no_future_access (which raises), a trap records a
    LEAK finding in .findings and still returns the first result - the
    demo wants the poisoned output and the diagnosis side by side.
    Per-call `view.as_of` values are kept in .calls as bookkeeping.
    Same assumptions and blind spot as assert_no_future_access.
    """

    def __init__(self, fn: Any, ledger: Ledger, clock: Any, steps: int = 1) -> None:
        self.fn = fn
        self.ledger = ledger
        self.clock = clock
        self.steps = steps
        self.findings: list[Finding] = []
        self.calls: list[Any] = []

    @property
    def has_leaks(self) -> bool:
        return any(f.severity == Severity.LEAK for f in self.findings)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(self.clock.now)
        first, second = _run_twice(self.fn, self.ledger, self.clock, self.steps, args, kwargs)
        if first != second:
            name = _fn_name(self.fn)
            self.findings.append(Finding(
                code="RUNTIME_LEAK",
                severity=Severity.LEAK,
                message=(
                    f"{name} gave {first[1]!r} under the clock and "
                    f"{second[0]} {second[1]!r} one probe-step later - with "
                    "nothing new knowable in between, that difference is "
                    "knowledge of the future (probe assumptions in the "
                    "lookahead module docstring)"
                ),
                where=f"leak_trap:{name}",
                detail={"first": repr(first[1]), "second": repr(second[1])},
            ))
        return first[1]
