"""nohindsight.live.fillsim — deterministic cruelty for your algo.

A fill emulator that lies to you in a reproducible way: partial fills that
still sum to your full size, rejections that come back as refusals instead
of exceptions, and latency that costs you a hair of price.  Same seed, same
orders, same bars -> same fills, every time.  A backtest whose fills you
cannot reproduce is a story, not a test.

Conventions (documented, not negotiable):

* **Market orders** fill at the bar's close, drifted adversely by latency:
  slip = latency_seconds * 1 bp (at the default 250 ms ceiling that is at
  most 0.25 bp — small, nonzero, and always against you).  Buys pay up,
  sells sell down.  The fill timestamp is bar ts + the drawn latency.
* **Limit orders** fill at the limit price exactly, only when the bar's
  range contains it (``low <= limit_price <= high``, buys and sells alike —
  the spec fixes the buy case; we mirror it for sells).  With only OHLC the
  intrabar path is unknowable, and filling at the limit is the conservative
  choice for both sides: any edge we report is understated, never inflated.
* **An unfilled limit order is not a refusal**: it returns ``([], None)``.
  We simulate one bar per call; keeping the order working is the caller's
  problem (a real broker would let it rest in the book).
* **Partial fills**: with probability ``partial_rate`` an order is sliced
  into 2-3 fills.  Slices are cut from the order quantity in Decimal space
  and the last slice is computed as the remainder, so partial fills sum
  EXACTLY to the order quantity — always, by construction.  Fractional
  shares are allowed; the sum is what must be exact.
* **Rejections**: with probability ``reject_rate`` an order is refused —
  no exception, just an empty fill list and a ``Refusal``.
* **RNG draw order per call** (fix your seed and this order is frozen):
  reject roll; then for a market order the latency draw, for a limit order
  the crossing test (no randomness) and — only if it crosses — the latency
  draw; then the partial roll, the slice count, and the cut points.
"""

from __future__ import annotations

import random
from datetime import timedelta
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional, Tuple

from ..core import to_utc
from .orders import Fill, LimitOrder, MarketOrder, Refusal, to_decimal

__all__ = ["FillEmulator"]

_BPS_PER_SECOND_OF_LATENCY = to_decimal(1)  # slip ceiling: 0.25 bp at 250 ms


@dataclass(frozen=True)
class _Latency:
    lo_ms: float
    hi_ms: float


class FillEmulator:
    """Seeded, deterministic fill simulator.

    Assumption worth stating: we fill against ONE bar you hand us.  We do
    not model the order book, queue position, or other participants — a
    daily OHLC bar does not contain that information, and pretending it
    does would be a leak in the opposite direction (inventing detail the
    data never had).
    """

    def __init__(
        self,
        seed: int = 0,
        partial_rate: float = 0.15,
        reject_rate: float = 0.02,
        latency_ms: Tuple[float, float] = (5, 250),
    ) -> None:
        lo, hi = latency_ms
        if not (0.0 <= lo <= hi):
            raise ValueError(f"latency_ms must satisfy 0 <= lo <= hi, got {latency_ms!r}")
        if not (0.0 <= partial_rate <= 1.0) or not (0.0 <= reject_rate <= 1.0):
            raise ValueError("partial_rate and reject_rate must be within [0, 1]")
        self._rng = random.Random(seed)
        self.partial_rate = float(partial_rate)
        self.reject_rate = float(reject_rate)
        self.latency_ms = _Latency(float(lo), float(hi))

    # -- public API -------------------------------------------------------

    def fill(
        self,
        order: MarketOrder | LimitOrder,
        bar: dict,
        ts=None,
    ) -> Tuple[list[Fill], Optional[Refusal]]:
        """Emulate one bar's worth of trading for `order`.

        `bar` needs the keys the order type implies: ``close`` for market
        orders, ``low``/``high`` for limit orders.  The fill timestamp comes
        from `ts` if given, else from ``bar["ts"]``; it is always stamped
        forward by the drawn latency.

        Returns ``(fills, refusal)``: fills are a list (possibly empty,
        never size-losing) and refusal is a ``Refusal`` or None.
        """
        when = to_utc(ts) if ts is not None else to_utc(bar["ts"])

        # Draw 1: the reject roll.
        if self._rng.random() < self.reject_rate:
            refusal = Refusal(
                order.order_id,
                "rejected",
                f"order {order.order_id} rejected by the fill emulator "
                f"(seeded reject_rate={self.reject_rate})",
            )
            return [], refusal

        if isinstance(order, LimitOrder):
            low, high = to_decimal(bar["low"]), to_decimal(bar["high"])
            limit = to_decimal(order.limit_price)
            if not (low <= limit <= high):
                # Not a refusal: the market simply never touched your price.
                return [], None
            price = limit
            latency = self._draw_latency()  # moves ts only; the price IS the limit
        elif isinstance(order, MarketOrder):
            latency = self._draw_latency()
            close = to_decimal(bar["close"])
            slip = to_decimal(latency) / to_decimal(1000) * _BPS_PER_SECOND_OF_LATENCY / to_decimal(10_000)
            price = close * (1 - slip) if order.qty < 0 else close * (1 + slip)
        else:
            raise TypeError(
                f"fill() wants a MarketOrder or LimitOrder, got {type(order).__name__}"
            )

        total = to_decimal(order.qty)
        sign = -1 if total < 0 else 1
        amount = -total if total < 0 else total

        # Draws 2+: partial roll, slice count, cut points.
        slices = self._slice(amount, self._slice_count())
        fill_ts = when + timedelta(milliseconds=latency)
        fills = [
            Fill(
                order_id=order.order_id,
                symbol=order.symbol,
                qty=sign * s,
                price=price,
                ts=fill_ts,
            )
            for s in slices
        ]
        return fills, None

    # -- internals ---------------------------------------------------------

    def _draw_latency(self) -> float:
        return self._rng.uniform(self.latency_ms.lo_ms, self.latency_ms.hi_ms)

    def _slice_count(self) -> int:
        if self._rng.random() < self.partial_rate:
            return self._rng.randint(2, 3)
        return 1

    def _slice(self, amount: Decimal, n: int) -> list[Decimal]:
        """Cut `amount` into n positive Decimal slices summing to it exactly.

        The first n-1 slices come from seeded cut points; the last slice is
        computed as ``amount - sum(previous)`` so the sum closes on the
        order quantity exactly (no fraction of a share is ever lost to
        rounding).  Exact for quantities up to Decimal's default 28
        significant digits — beyond that is not a quantity, it's a typo.
        """
        if n <= 1 or amount <= 0:
            return [amount]
        cuts = self._draw_cuts(amount, n - 1)
        if cuts is None:  # degenerate draws; do not risk a zero slice
            return [amount]
        slices: list[Decimal] = []
        acc = Decimal(0)
        for cut in cuts:
            s = cut - acc
            slices.append(s)
            acc = acc + s
        slices.append(amount - acc)
        return slices

    def _draw_cuts(self, amount: Decimal, k: int) -> Optional[list[Decimal]]:
        """k strictly increasing cut points strictly inside (0, amount)."""
        for _ in range(8):  # bounded redraws keep determinism without deadlock
            fracs = sorted(self._rng.random() for _ in range(k))
            if fracs[0] <= 0.0 or len(set(fracs)) != k:
                continue
            cuts = [amount * to_decimal(f) for f in fracs]
            if cuts[0] > 0 and all(cuts[i] < cuts[i + 1] for i in range(k - 1)) and cuts[-1] < amount:
                return cuts
        return None
