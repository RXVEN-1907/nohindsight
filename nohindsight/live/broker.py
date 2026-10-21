"""nohindsight.live.broker — decimal-exact paper money.

Float drift is a crime in a repo about honesty.  Every cash, position, fee
and equity number here is a ``decimal.Decimal``; prices and quantities
cross the boundary through ``to_decimal`` (i.e. ``Decimal(str(x))``).

Margin is refused: cash must stay >= 0 after any trade.  Shorting is
allowed — a short *credits* cash (you sold something) — but there is no
margin loan: a trade that would push cash negative is refused whole, never
partially filled into debt.  We emulate the fills first, then commit only
if the post-trade cash is >= 0; an unaffordable fill costs nothing.

The cost seam: `cost_model` is any object with

    fee_for(notional: Decimal, shares: Decimal) -> Decimal

We deliberately do NOT import nohindsight.costs here — live runs
standalone while the costs module is built in parallel, and a thin adapter
into this protocol lands with the integration module.  ``FlatFee`` below
implements the protocol so the broker is usable today.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from typing import Optional, Tuple

from ..core import to_utc
from .fillsim import FillEmulator
from .orders import Fill, MarketOrder, Refusal, to_decimal
from .risk import AccountState

__all__ = ["FlatFee", "PaperBroker"]


class FlatFee:
    """Minimal stand-in implementing the live fee protocol: a flat bps rate
    on notional.  Exact in Decimal for bps values with a terminating
    decimal expansion (which is all of them a human would type)."""

    def __init__(self, bps: float) -> None:
        self.bps = to_decimal(bps)

    def fee_for(self, notional: Decimal, shares: Decimal) -> Decimal:
        return notional * self.bps / to_decimal(10_000)


class PaperBroker:
    """A paper broker that keeps the books exactly.

    Two ways to drive it:

    * **Standalone**: feed bars with ``on_bar(symbol, bar, ts)``, then
      ``submit(order)`` — the order executes against the latest bar.
    * **Under Replay**: the replay's Ctx calls ``_execute(order, bar, ts)``
      directly with the explicit bar, so there is never a stale-market
      hazard.  (Yes, a leading underscore — the spec names it, so it is a
      published seam, not a secret.)

    The equity curve records one mark per distinct timestamp, strictly
    increasing: marks come from ``on_bar``/``mark`` and from executions,
    and a repeat timestamp never appends twice.
    """

    def __init__(
        self,
        starting_cash: float,
        cost_model=None,
        fill_emulator: Optional[FillEmulator] = None,
    ) -> None:
        cash = to_decimal(starting_cash)
        if cash < 0:
            raise ValueError(f"starting_cash must be >= 0, got {starting_cash}")
        self._cash = cash
        self.cost_model = cost_model
        self.emulator = fill_emulator if fill_emulator is not None else FillEmulator()
        self._positions: dict[str, Decimal] = {}
        self._trades: list[Fill] = []
        self._refusals: list[Refusal] = []
        self._curve: list[Tuple[datetime, Decimal]] = []
        self._last_prices: dict[str, Decimal] = {}
        self._current_bars: dict[str, dict] = {}
        self._bar_ts: Optional[datetime] = None

    # -- market feed (standalone path) -------------------------------------

    def on_bar(self, symbol: str, bar: dict, ts=None) -> None:
        """Tell the broker what the market shows for `symbol`.

        `ts` may be explicit or live on the bar as ``bar["ts"]``; it stamps
        the equity-curve mark.  Bars without any timestamp still update
        market state but cannot be marked in time.
        """
        when = ts if ts is not None else bar.get("ts")
        if when is not None:
            self._bar_ts = to_utc(when)
        self._current_bars[symbol] = bar
        self._last_prices[symbol] = to_decimal(bar["close"])
        if self._bar_ts is not None:
            self._curve_point(self._bar_ts)

    def mark(self, prices: dict, ts) -> None:
        """Update last-known prices and append an equity-curve mark at ts.

        Replay calls this once per bar so the curve covers every bar, not
        just bars where something traded.
        """
        when = to_utc(ts)
        for symbol, price in prices.items():
            self._last_prices[symbol] = to_decimal(price)
        self._curve_point(when)

    # -- order flow ---------------------------------------------------------

    def submit(self, order: MarketOrder) -> Tuple[list[Fill], Optional[Refusal]]:
        """Execute `order` against the latest bar fed via on_bar."""
        bar = self._current_bars.get(order.symbol)
        if bar is None:
            raise ValueError(
                f"no market data for {order.symbol!r} — call on_bar() before submitting"
            )
        if self._bar_ts is None:
            raise ValueError(
                "no market timestamp — feed on_bar() a bar carrying 'ts' or an explicit ts"
            )
        return self._execute(order, bar, self._bar_ts)

    def _execute(
        self, order: MarketOrder, bar: dict, ts
    ) -> Tuple[list[Fill], Optional[Refusal]]:
        """Emulate fills for `order` against `bar` at time `ts`, then commit
        them atomically or refuse.

        Atomicity rule: an unaffordable order is refused WHOLE.  We price
        the emulated fills first (including fees), check that cash stays
        >= 0, and only then touch the books.
        """
        when = to_utc(ts)
        fills, refusal = self.emulator.fill(order, bar, ts=when)
        if refusal is not None:
            self._refusals.append(refusal)
            return [], refusal
        if not fills:  # an unfilled limit order: nothing happened, nothing refused
            return [], None

        priced: list[Fill] = []
        projected = self._cash
        for f in fills:
            notional = abs(f.qty) * f.price
            fee = self._fee_for(notional, abs(f.qty))
            f = replace(f, fee=fee)
            priced.append(f)
            projected -= f.qty * f.price  # signed: buys drain, sells credit
            projected -= fee  # fees always drain

        if projected < 0:
            refusal = Refusal(
                order.order_id,
                "buying_power",
                f"order {order.order_id} would take cash to {projected} — "
                "margin is refused",
            )
            self._refusals.append(refusal)
            return [], refusal

        for f in priced:
            self._cash = self._cash - f.qty * f.price - f.fee
            self._positions[f.symbol] = self._positions.get(f.symbol, Decimal(0)) + f.qty
            self._last_prices[f.symbol] = f.price
        self._trades.extend(priced)
        self._curve_point(when)
        return priced, None

    # -- books ---------------------------------------------------------------

    @property
    def cash(self) -> Decimal:
        return self._cash

    def position(self, symbol: str) -> Decimal:
        """Signed position in `symbol` (negative = short).  Zero if never traded."""
        return self._positions.get(symbol, Decimal(0))

    def equity(self, prices: dict) -> Decimal:
        """cash + sum(position * price).  Every held symbol must be priced —
        silently skipping a position would be a quiet lie, so a missing
        price raises KeyError naming the symbol."""
        total = self._cash
        for symbol, qty in self._positions.items():
            if symbol not in prices:
                raise KeyError(f"equity() needs a price for held symbol {symbol!r}")
            total = total + qty * to_decimal(prices[symbol])
        return total

    @property
    def trades(self) -> list[Fill]:
        return list(self._trades)

    @property
    def refusals(self) -> list[Refusal]:
        return list(self._refusals)

    @property
    def equity_curve(self) -> list[Tuple[datetime, Decimal]]:
        return list(self._curve)

    def account_state(self) -> AccountState:
        """Snapshot for RiskGuard.approve()."""
        return AccountState(
            cash=self._cash,
            equity=self.equity(self._last_prices),
            positions=dict(self._positions),
        )

    # -- internals -------------------------------------------------------------

    def _fee_for(self, notional: Decimal, shares: Decimal) -> Decimal:
        if self.cost_model is None:
            return Decimal(0)
        return to_decimal(self.cost_model.fee_for(notional, shares))

    def _curve_point(self, ts: datetime) -> None:
        if self._curve and ts <= self._curve[-1][0]:
            return  # one mark per timestamp, strictly increasing
        self._curve.append((ts, self.equity(self._last_prices)))
