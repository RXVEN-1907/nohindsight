"""nohindsight.data.universe - who existed, and when.

The survivorship question, as data.  A backtest that picks its symbols from
today's list only ever trades survivors; the ghosts - companies that listed,
died, and were quietly erased from the menu - never got traded, so the
results are flattered.  The report exists so a backtester can ask out loud:
"did I trade a world that only contains survivors?"
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from ..core import to_utc

__all__ = ["Membership", "Universe"]


@dataclass(frozen=True)
class Membership:
    """One listing spell.  Times are coerced to aware UTC datetimes."""

    symbol: str
    listed: datetime
    delisted: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "listed", to_utc(self.listed))
        if self.delisted is not None:
            object.__setattr__(self, "delisted", to_utc(self.delisted))


class Universe:
    """A set of listing spells, one per symbol.

    Boundary convention: a symbol is a member on its listing day (inclusive)
    and is not a member on its delisting day (exclusive).  Re-adding a
    symbol raises - silently replacing a listing history is exactly how
    survivorship bias sneaks in unnoticed.
    """

    def __init__(self) -> None:
        self._members: dict[str, Membership] = {}

    def add(
        self,
        symbol: str,
        listed: datetime | str,
        delisted: datetime | str | None = None,
    ) -> Membership:
        """Record a listing; times may be datetimes or ISO strings."""
        if symbol in self._members:
            prior = self._members[symbol]
            raise ValueError(
                f"{symbol!r} already in universe (listed "
                f"{prior.listed.isoformat()}) - one spell per symbol"
            )
        m = Membership(symbol=symbol, listed=listed, delisted=delisted)
        self._members[symbol] = m
        return m

    def members_at(self, as_of: datetime | str) -> list[str]:
        """Symbols that existed at as_of: listed <= as_of and not yet gone."""
        when = to_utc(as_of)
        return sorted(
            m.symbol
            for m in self._members.values()
            if m.listed <= when and (m.delisted is None or when < m.delisted)
        )

    def delisted_between(self, start: datetime | str, end: datetime | str) -> list[str]:
        """Symbols whose delisting falls in [start, end] - both ends inclusive."""
        lo, hi = to_utc(start), to_utc(end)
        return sorted(
            m.symbol
            for m in self._members.values()
            if m.delisted is not None and lo <= m.delisted <= hi
        )

    def survivorship_report(self, as_of: datetime | str) -> dict[str, int]:
        """Counts as of one instant: {"current", "ever_listed", "ghosts"}.

        ever_listed  symbols that had listed by as_of
        current      still listed at as_of
        ghosts       listed in the past, gone by as_of - the companies a
                     survivorship-biased backtest silently never traded
        """
        when = to_utc(as_of)
        ever = [m for m in self._members.values() if m.listed <= when]
        current = [m for m in ever if m.delisted is None or when < m.delisted]
        return {
            "current": len(current),
            "ever_listed": len(ever),
            "ghosts": len(ever) - len(current),
        }

    def __len__(self) -> int:
        return len(self._members)

    def __contains__(self, symbol: str) -> bool:
        return symbol in self._members
