"""nohindsight.live.orders - the paper-trade vocabulary.

Orders are intents; fills are facts; refusals are the system saying no.

Sign convention, everywhere in ``live``: ``qty > 0`` buys, ``qty < 0``
sells.  One sign, no "side" enum to get wrong.

Money crosses this module as ``decimal.Decimal``.  Anything a caller hands
us (int, float, str, Decimal) is coerced through ``to_decimal`` -
``Decimal(str(x))`` keeps the shortest repr you wrote as the exact number
you meant, which is the whole defense against float drift.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Union

__all__ = ["MarketOrder", "LimitOrder", "Fill", "Refusal", "to_decimal"]

Qty = Union[int, float, Decimal]

_seq = itertools.count(1)


def _next_id() -> str:
    """Process-unique order id.  Deterministic within a run; paper trading
    does not need UUIDs, it needs ids that sort and count."""
    return f"ord-{next(_seq):06d}"


def to_decimal(x: Qty) -> Decimal:
    """Coerce int/float/str/Decimal into a Decimal without float drift.

    Floats go through ``str()`` so 0.1 becomes exactly Decimal("0.1"), not
    the 0.1000000000000000055511... a float actually is.
    """
    if isinstance(x, Decimal):
        return x
    return Decimal(str(x))


@dataclass(frozen=True)
class MarketOrder:
    """Fill me now at whatever the market shows."""

    symbol: str
    qty: Qty  # sign = direction
    order_id: str = field(default_factory=_next_id)


@dataclass(frozen=True)
class LimitOrder:
    """Fill me only if the bar's range touches limit_price.

    Fill rule (see fillsim): low <= limit_price <= high, buys and sells
    alike; the fill happens at the limit price itself.
    """

    symbol: str
    qty: Qty  # sign = direction
    limit_price: Qty
    order_id: str = field(default_factory=_next_id)


@dataclass(frozen=True)
class Fill:
    """One executed slice of one order.

    qty and price are Decimal - money math never touches float here.  fee
    is charged in cash on every fill: buys pay it on top of the notional,
    sells net it out of the proceeds.
    """

    order_id: str
    symbol: str
    qty: Decimal
    price: Decimal
    ts: datetime
    fee: Decimal = Decimal("0")


@dataclass(frozen=True)
class Refusal:
    """A 'no' with its reason attached.

    Every refusal names the rule that produced it so reports can count
    which rule saved you (or nagged you) - drawdown, buying power, fat
    finger, order rate, or the fill emulator's own seeded rejections.
    """

    order_id: str
    rule: str
    message: str
