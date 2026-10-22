"""nohindsight.live.risk - the kill switch.

The risk guard sits between the strategy and the broker.  Every order is
interrogated before it can touch money:

    1. kill switch (latched)  - a blown-up account never trades again
    2. drawdown               - breach of max_drawdown from the high-water mark
    3. order rate             - more than max_orders_per_bar submissions in a bar
    4. fat-finger price       - a limit far from the last traded price
    5. fat-finger qty         - an order far larger than the symbol's typical
                                approved size (learned on the job)
    6. position limit         - |position after order| * last price beyond
                                max_position_pct of equity

The latch is the point.  Risk limits that reset when you blow through them
are how accounts die twice.  With ``kill_switch_latch=True`` (the default)
a drawdown breach refuses everything FOREVER - the only way back is a new
guard, which is exactly as forgiving as a new account should be.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Mapping, Optional, Union

from ..core import to_utc
from .orders import LimitOrder, Refusal, to_decimal

__all__ = ["RiskConfig", "AccountState", "RiskGuard"]


@dataclass(frozen=True)
class RiskConfig:
    """Policy numbers, plain floats - they are thresholds, not money."""

    max_drawdown: float = 0.15
    max_position_pct: float = 0.25
    fat_finger_price_mult: float = 5.0
    fat_finger_qty_mult: float = 20.0
    max_orders_per_bar: int = 10
    kill_switch_latch: bool = True


@dataclass(frozen=True)
class AccountState:
    """A snapshot of the account as risk sees it.

    `equity` is marked to the last known prices; `positions` maps symbol to
    signed Decimal size.  PaperBroker.account_state() produces these; tests
    can build them by hand.
    """

    cash: Decimal
    equity: Decimal
    positions: Mapping[str, Decimal] = field(default_factory=dict)


class RiskGuard:
    """Approve or refuse orders, and remember the worst.

    Drawdown is measured from the highest equity THIS GUARD has ever seen -
    the account's high-water mark as observed by risk, not one the strategy
    self-reports.  Fat-finger qty is measured against a running mean of
    approved sizes per symbol (a median would be nicer; the mean is honest
    enough and simpler), so the FIRST order in a symbol is never qty-refused
    - there is nothing to compare it against yet.  Price fat-fingers still
    apply to that first order.

    Order rate needs bar boundaries: Replay calls begin_bar() once per
    step.  Without it (standalone use) rate limiting is disabled - a guard
    that cannot see bars refuses to pretend it can count them.
    """

    def __init__(self, config: Optional[RiskConfig] = None) -> None:
        self.config = config if config is not None else RiskConfig()
        self._latched = False
        self._hwm: Optional[Decimal] = None
        self._typical: dict[str, Decimal] = {}
        self._bar_ts = None
        self._submissions_this_bar = 0

    @property
    def latched(self) -> bool:
        """True once the kill switch has fired.  It does not un-latch."""
        return self._latched

    def begin_bar(self, ts) -> None:
        """Mark the start of a bar; resets the per-bar submission count."""
        self._bar_ts = to_utc(ts)
        self._submissions_this_bar = 0

    def approve(
        self,
        order: Union["LimitOrder", object],
        account_state: AccountState,
        last_price: Optional[Union[Decimal, float, int]],
    ) -> Optional[Refusal]:
        """Return a Refusal if the order may not trade, else None.

        Checks run in the order listed on the class; the first refusal
        wins.  Prices are valued at `last_price` (the close of the bar the
        strategy just saw); a None last_price skips the price-dependent
        rules rather than guessing a price.
        """
        # 1. The latch answers everything.
        if self._latched:
            return Refusal(
                order.order_id,
                "kill_switch",
                "kill switch latched: this account blew through its drawdown "
                "limit and does not trade again",
            )

        # 2. Drawdown from the high-water mark.
        equity = to_decimal(account_state.equity)
        if self._hwm is None or equity > self._hwm:
            self._hwm = equity
        if self._hwm is not None and self._hwm > 0:
            drawdown = (self._hwm - equity) / self._hwm
            if drawdown > to_decimal(self.config.max_drawdown):
                if self.config.kill_switch_latch:
                    self._latched = True
                return Refusal(
                    order.order_id,
                    "max_drawdown",
                    f"drawdown {float(drawdown):.2%} from high-water mark "
                    f"{self._hwm} breaches max_drawdown "
                    f"{self.config.max_drawdown:.2%}",
                )

        # 3. Order rate per bar.
        if self._bar_ts is not None:
            self._submissions_this_bar += 1
            if self._submissions_this_bar > self.config.max_orders_per_bar:
                return Refusal(
                    order.order_id,
                    "order_rate",
                    f"{self._submissions_this_bar} submissions this bar "
                    f"exceed max_orders_per_bar={self.config.max_orders_per_bar}",
                )

        last = to_decimal(last_price) if last_price is not None else None

        # 4. Fat-finger price (limit orders only - market orders carry no price).
        if isinstance(order, LimitOrder) and last is not None and last > 0:
            limit = to_decimal(order.limit_price)
            mult = to_decimal(self.config.fat_finger_price_mult)
            if limit > last * mult or limit < last / mult:
                return Refusal(
                    order.order_id,
                    "fat_finger_price",
                    f"limit {limit} is more than {self.config.fat_finger_price_mult:g}x "
                    f"away from last traded price {last} - that is a typo, not a view",
                )

        # 5. Fat-finger qty vs this symbol's typical approved size.
        qty = abs(to_decimal(order.qty))
        typical = self._typical.get(order.symbol)
        if typical is not None and typical > 0:
            if qty > typical * to_decimal(self.config.fat_finger_qty_mult):
                return Refusal(
                    order.order_id,
                    "fat_finger_qty",
                    f"order of {qty} is more than {self.config.fat_finger_qty_mult:g}x "
                    f"the typical approved size ({typical}) for {order.symbol}",
                )

        # 6. Position limit, valued at the last traded price.
        if last is not None and last > 0:
            after = to_decimal(account_state.positions.get(order.symbol, 0)) + to_decimal(order.qty)
            if abs(after) * last > to_decimal(self.config.max_position_pct) * equity:
                return Refusal(
                    order.order_id,
                    "position_limit",
                    f"position after order would be {after} {order.symbol} "
                    f"(value {abs(after) * last}), beyond "
                    f"{self.config.max_position_pct:.2%} of equity {equity}",
                )

        # Approved.  Only approved sizes teach the guard what "typical" is -
        # refusals must not normalize madness.
        self._typical[order.symbol] = qty if typical is None else (typical + qty) / 2
        return None
