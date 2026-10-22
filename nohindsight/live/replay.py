"""nohindsight.live.replay - the honest backtest loop.

Replay walks a bar ledger forward in time and hands the strategy ONLY an
AsOfView (plus a Ctx that routes orders through risk into the broker).  The
strategy structurally cannot read the raw ledger - it never receives it.
The guard is not a lint rule, it is a type.

One honest caveat, noted because this library is about honesty: the view
exposes the clock it is bound to, so a hostile strategy could move time
itself.  The guard is against ACCIDENTAL leakage - reading data the world
hadn't produced yet - which is the kind that actually happens.  Adversaries
are out of scope.

Assumptions Replay makes about the ledger (stated, not hidden):

* Bar entries are keyed by symbol; the value is the bar dict.
* A bar's knowledge_time equals its event_time - the close is knowable at
  the close.  A ledger that knows its bars LATER than they happen will
  raise LeakError here, loudly; that is the guard doing its job.
* The clock must start at or before the first event time.  Clock cannot
  rewind, so Replay refuses up front rather than silently dropping bars.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Callable, Optional

from ..core import AsOfView, Entry, Ledger, to_utc
from .broker import PaperBroker
from .orders import LimitOrder, MarketOrder, Refusal, to_decimal
from .risk import RiskGuard

__all__ = ["Ctx", "Replay", "ReplayResult"]


@dataclass
class ReplayResult:
    """What a run produced.  Refusals are first-class output: a backtest
    that never gets refused is a backtest whose risk guard you never tested."""

    n_bars: int
    n_orders: int
    n_fills: int
    refusals: list[Refusal] = field(default_factory=list)
    equity_curve: list[tuple[datetime, Decimal]] = field(default_factory=list)


class Ctx:
    """Everything a strategy may touch on one bar: now, the order gate, and
    the refusals it has earned so far.

    `bars` holds the bar dicts that arrived THIS step (already read through
    the AsOfView, so they carry no future).  Submitting an order for a
    symbol that did not arrive is refused ("no_market") - you can only
    trade a symbol the view just showed you.
    """

    def __init__(self, replay: "Replay", ts: datetime, bars: dict) -> None:
        self._replay = replay
        self._ts = ts
        self.bars = dict(bars)
        self.refusals: list[Refusal] = []

    @property
    def now(self) -> datetime:
        """The bar time this context belongs to (frozen at creation)."""
        return self._ts

    def submit(self, order: MarketOrder | LimitOrder) -> Optional[Refusal]:
        """Route an order through risk into the broker.

        Returns the Refusal if the order was turned away (by risk, by the
        fill emulator, or by buying power), else None.
        """
        bar = self.bars.get(order.symbol)
        if bar is None:
            refusal = Refusal(
                order.order_id,
                "no_market",
                f"no bar for {order.symbol!r} arrived at {self._ts.isoformat()} - "
                "you can only trade a symbol the view just showed you",
            )
            self._record(refusal)
            return refusal

        last_price = to_decimal(bar["close"])
        if self._replay.risk is not None:
            refusal = self._replay.risk.approve(
                order, self._replay.broker.account_state(), last_price
            )
            if refusal is not None:
                self._record(refusal)
                return refusal

        fills, refusal = self._replay.broker._execute(order, bar, self._ts)
        self._replay._n_orders += 1
        if refusal is not None:
            self._record(refusal)
            return refusal
        for f in fills:
            self._replay._emit({"type": "fill", "fill": f})
        return None

    def _record(self, refusal: Refusal) -> None:
        self.refusals.append(refusal)
        self._replay.refusals.append(refusal)
        self._replay._emit({"type": "refusal", "refusal": refusal})


class Replay:
    """Drive `strategy` over `ledger`, one event time at a time, guarded.

    The strategy receives ``(view, ctx)`` per bar and nothing else: the
    view is an AsOfView bound to the replay clock (reading the future
    raises LeakError), and ctx.submit() is the only door to the broker -
    and it opens through the risk guard.
    """

    def __init__(
        self,
        ledger: Ledger,
        clock,
        strategy,
        broker: PaperBroker,
        risk: Optional[RiskGuard] = None,
        on_event: Optional[Callable[[dict], None]] = None,
    ) -> None:
        self.ledger = ledger
        self.clock = clock
        self.strategy = strategy
        self.broker = broker
        self.risk = risk
        self.on_event = on_event
        self.refusals: list[Refusal] = []
        self._n_orders = 0

    def _emit(self, event: dict) -> None:
        if self.on_event is not None:
            self.on_event(event)

    def run(self) -> ReplayResult:
        """Run the whole ledger forward.  See the module docstring for the
        ledger assumptions; violations raise, honestly."""
        by_ts: dict[datetime, list[Entry]] = {}
        for entry in self.ledger.entries():
            by_ts.setdefault(entry.event_time, []).append(entry)
        times = sorted(by_ts)
        if not times:
            return ReplayResult(n_bars=0, n_orders=0, n_fills=0)

        first = times[0]
        if self.clock.now > first:
            raise ValueError(
                f"clock starts at {self.clock.now.isoformat()}, after the first "
                f"bar at {first.isoformat()} - start the clock at or before "
                "the data; Clock cannot rewind"
            )

        view: AsOfView = self.ledger.view(self.clock)
        last_close: dict[str, Decimal] = {}

        for ts in times:
            self.clock.advance_to(ts)
            if self.risk is not None:
                begin = getattr(self.risk, "begin_bar", None)
                if begin is not None:
                    begin(ts)
            # Bars arriving now, read ONLY through the guarded view.
            arriving = {entry.key: view.get(entry.key) for entry in by_ts[ts]}
            ctx = Ctx(self, ts, arriving)
            self._emit({"type": "bar", "ts": ts})
            self.strategy.on_bar(view, ctx)
            for symbol, bar in arriving.items():
                if isinstance(bar, dict) and "close" in bar:
                    last_close[symbol] = to_decimal(bar["close"])
            self.broker.mark(last_close, ts)

        return ReplayResult(
            n_bars=len(times),
            n_orders=self._n_orders,
            n_fills=len(self.broker.trades),
            refusals=list(self.refusals),
            equity_curve=self.broker.equity_curve,
        )
