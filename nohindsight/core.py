"""nohindsight.core - the two clocks and the guard.

Every datum in nohindsight carries two times:

    event_time      when the thing happened in the world
    knowledge_time  when an honest observer could have first known it

A backtest that reads data whose knowledge_time is after "now" has leaked:
it used information the future hadn't produced yet.  The guard makes that a
hard error, not a warning.  Data access goes through an AsOfView attached to
a Clock; the view physically refuses to show you anything the clock hasn't
seen happen.  There is no way to peek from a view - that's the point.

Zero dependencies.  Everything in nohindsight is built on this file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Optional, Union

__all__ = [
    "UTC",
    "to_utc",
    "LeakError",
    "Entry",
    "Clock",
    "Ledger",
    "AsOfView",
    "AsOfKeyError",
]

UTC = timezone.utc
_TimeLike = Union[datetime, str, None]


def to_utc(value: _TimeLike) -> datetime:
    """Coerce datetime/ISO-string into an aware UTC datetime.

    Naive datetimes are assumed to be UTC (documented assumption: naive
    timestamps in finance data are already ambiguous enough; we pick UTC
    and say so rather than guess a market timezone for you).
    """
    if value is None:
        raise ValueError("expected a datetime or ISO-8601 string, got None")
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
    else:
        raise TypeError(f"cannot interpret {value!r} as a time")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class LeakError(Exception):
    """Raised when code tries to read data the clock hasn't seen yet.

    Attributes carry the full story so the report layer can print exactly
    which datum leaked, by how much, and through which key.
    """

    def __init__(
        self,
        message: str,
        *,
        key: Optional[str] = None,
        knowledge_time: Optional[datetime] = None,
        as_of: Optional[datetime] = None,
    ) -> None:
        super().__init__(message)
        self.key = key
        self.knowledge_time = knowledge_time
        self.as_of = as_of

    @classmethod
    def future_read(cls, key: str, knowledge_time: datetime, as_of: datetime) -> "LeakError":
        delta = knowledge_time - as_of
        return cls(
            f"LEAK via {key!r}: first knowable at {knowledge_time.isoformat()} "
            f"but the clock says {as_of.isoformat()} "
            f"({delta} into the future).",
            key=key,
            knowledge_time=knowledge_time,
            as_of=as_of,
        )


@dataclass(frozen=True)
class Entry:
    """One revision of one datum.  Immutable."""

    key: str
    value: Any
    event_time: datetime
    knowledge_time: datetime
    revision: int = 0

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("key must be non-empty")
        if self.knowledge_time < self.event_time:
            raise ValueError(
                f"impossible entry for {self.key!r}: knowledge_time "
                f"({self.knowledge_time.isoformat()}) precedes event_time "
                f"({self.event_time.isoformat()}) - the world can't know "
                "things before they happen"
            )

    def at(self, when: datetime) -> bool:
        """Is this revision knowable at `when`?"""
        return self.knowledge_time <= when


class AsOfKeyError(KeyError):
    """The honest 'not found': no revision of `key` was knowable yet.

    Before the first filing, the fundamental does not exist - it is not
    None, not zero, not forward-filled.  This error is the data saying so.
    """


class Clock:
    """A clock you can freeze, fork, and walk.  Backtests use this instead
    of datetime.now(); the present is whatever the clock says it is.

    Time only moves forward.  Trying to rewind raises ValueError - a
    backtest that rewinds its clock is a time machine, not a backtest.
    """

    def __init__(self, start: _TimeLike = None) -> None:
        self._t: datetime = to_utc(start) if start is not None else datetime.now(UTC)

    @property
    def now(self) -> datetime:
        return self._t

    def advance_to(self, when: _TimeLike) -> datetime:
        target = to_utc(when) if not isinstance(when, datetime) else when
        if target.tzinfo is None:
            target = target.replace(tzinfo=UTC)
        if target < self._t:
            raise ValueError(
                f"clock cannot rewind from {self._t.isoformat()} to {target.isoformat()}"
            )
        self._t = target
        return self._t

    def advance(self, delta: timedelta) -> datetime:
        if delta < timedelta(0):
            raise ValueError("clock cannot rewind")
        self._t = self._t + delta
        return self._t

    def fork(self) -> "Clock":
        """An independent clock starting at the same instant."""
        return Clock(self._t)


class Ledger:
    """An append-only, keyed, revisioned series.

    Appending a key again creates the next revision; nothing is ever
    overwritten or deleted.  `value_at` answers the only honest question:
    'what did an observer at time T know about this key?'  - the answer is
    the latest revision whose knowledge_time <= T, or AsOfKeyError if none.
    """

    def __init__(self, name: str) -> None:
        if not name:
            raise ValueError("ledger needs a name")
        self.name = name
        self._entries: list[Entry] = []
        self._by_key: dict[str, list[Entry]] = {}

    # -- writing ---------------------------------------------------------

    def append(
        self,
        key: str,
        value: Any,
        event_time: _TimeLike,
        knowledge_time: _TimeLike,
    ) -> Entry:
        et, kt = to_utc(event_time), to_utc(knowledge_time)
        prior = self._by_key.get(key, [])
        revision = len(prior)
        entry = Entry(key=key, value=value, event_time=et, knowledge_time=kt, revision=revision)
        # per-key list kept sorted by (knowledge_time, revision)
        idx = bisect_right_kt(prior, kt, revision)
        prior.insert(idx, entry)
        self._by_key[key] = prior
        self._entries.append(entry)
        return entry

    # -- reading (unguarded: raw ledger, use with care) -------------------

    def history(self, key: str) -> list[Entry]:
        """Every revision ever appended for key, in knowledge order."""
        return list(self._by_key.get(key, []))

    def keys(self) -> list[str]:
        return list(self._by_key.keys())

    def entries(self) -> tuple[Entry, ...]:
        return tuple(self._entries)

    def entries_after(self, when: datetime) -> list[Entry]:
        """Raw access to everything NOT knowable at `when`.

        This is the unsafe side of the ledger.  The checks module uses it
        to measure how much future there was to leak; strategies never
        touch it.
        """
        when = to_utc(when)
        return [e for e in self._entries if e.knowledge_time > when]

    def __len__(self) -> int:
        return len(self._entries)

    # -- reading (the honest way) -----------------------------------------

    def value_at(self, key: str, as_of: Union[datetime, Clock, None] = None) -> Any:
        """Latest revision of `key` knowable at as_of (default: real now).

        Raises AsOfKeyError if no revision was knowable yet - that is the
        truthful answer, not a filled value.
        """
        when = _resolve_when(as_of)
        prior = self._by_key.get(key, [])
        for entry in reversed(prior):
            if entry.at(when):
                return entry.value
        raise AsOfKeyError(
            f"{key!r}: no revision knowable at {when.isoformat()} "
            f"({len(prior)} revisions exist, first at "
            f"{prior[0].knowledge_time.isoformat() if prior else '-'})"
        )

    def revisions_known(self, key: str, as_of: Union[datetime, Clock, None] = None) -> list[Entry]:
        """Revisions of key knowable at as_of, oldest first.

        Two revisions known at once means: 'the number changed and the
        world had noticed by then' - a restatement in progress.
        """
        when = _resolve_when(as_of)
        return [e for e in self._by_key.get(key, []) if e.at(when)]

    def view(self, clock: Clock) -> "AsOfView":
        """A guarded window onto this ledger, bound to `clock`."""
        return AsOfView(self, clock)


def _resolve_when(as_of: Union[datetime, Clock, None]) -> datetime:
    if as_of is None:
        return datetime.now(UTC)
    if isinstance(as_of, Clock):
        return as_of.now
    return to_utc(as_of)


def bisect_right_kt(entries: list[Entry], kt: datetime, revision: int) -> int:
    """Insertion index keeping entries sorted by (knowledge_time, revision)."""
    lo, hi = 0, len(entries)
    while lo < hi:
        mid = (lo + hi) // 2
        e = entries[mid]
        if (e.knowledge_time, e.revision) <= (kt, revision):
            lo = mid + 1
        else:
            hi = mid
    return lo


class AsOfView:
    """A window on a Ledger that refuses to show the future.

    This is what strategies receive.  Every read is stamped against the
    view's clock; a read of data first knowable later than now raises
    LeakError.  There is deliberately no method to bypass the guard.
    """

    def __init__(self, ledger: Ledger, clock: Clock) -> None:
        self._ledger = ledger
        self._clock = clock

    @property
    def as_of(self) -> datetime:
        return self._clock.now

    @property
    def clock(self) -> Clock:
        return self._clock

    def get(self, key: str) -> Any:
        """Honest read.  AsOfKeyError if nothing knowable yet."""
        when = self._clock.now
        prior = self._ledger.history(key)
        for entry in reversed(prior):
            if entry.at(when):
                return entry.value
        if prior:
            first = prior[0]
            raise LeakError.future_read(key, first.knowledge_time, when)
        raise AsOfKeyError(f"{key!r} does not exist in ledger {self._ledger.name!r}")

    def get_or(self, key: str, default: Any = None) -> Any:
        try:
            return self.get(key)
        except (AsOfKeyError, LeakError):
            return default

    def known_keys(self) -> list[str]:
        """Keys with at least one revision knowable now.

        Iterate over THIS, never over ledger.keys(): the ledger's full key
        list includes companies that hadn't listed yet and series that
        started later - iterating it is survivorship bias with extra steps.
        """
        when = self._clock.now
        out = []
        for key, prior in self._ledger._by_key.items():
            if prior and prior[0].knowledge_time <= when:
                out.append(key)
        return out

    def revisions(self, key: str) -> list[Entry]:
        """Revisions of key knowable now (a restatement in progress shows up here)."""
        when = self._clock.now
        return [e for e in self._ledger.history(key) if e.at(when)]

    def __contains__(self, key: str) -> bool:
        try:
            self.get(key)
            return True
        except (AsOfKeyError, LeakError):
            return False

    def __getitem__(self, key: str) -> Any:
        return self.get(key)

    def __iter__(self) -> Iterator[str]:
        return iter(self.known_keys())
