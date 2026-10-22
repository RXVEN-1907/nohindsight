"""nohindsight.costs.model - the reality tax, itemized.

A backtest without costs is a fantasy with good marketing.  Every trade
pays four honest lines:

    fee           what your broker charges            (FeeSchedule)
    spread_cost   what crossing the spread costs      (SpreadModel)
    impact_cost   what moving the market costs        (ImpactModel)
    borrow_cost   what being short overnight costs    (BorrowModel)

Impact follows the square-root law - impact_bps = coeff * sigma_bps *
sqrt(qty / adv) - the empirical regularity documented by Bouchaud,
Farmer & Lillo's studies of order flow and formalized in the optimal
execution framework of Almgren & Chriss (2000): quadruple the size,
double the impact.

Costs is the one department that needs no clock: a Trade carries its own
holding_days and nothing here reads data, so nothing here can peek at
the future.  The dishonesty costs enable is not lookahead - it is the
quiet subtraction that turns a real edge into noise.  fantasy_vs_real
exists to make that subtraction loud.

Zero dependencies beyond the standard library.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence, Union

__all__ = [
    "Trade",
    "CostBreakdown",
    "FeeSchedule",
    "SpreadModel",
    "ImpactModel",
    "BorrowModel",
    "CostModel",
    "fantasy_vs_real",
]

_SIDES = ("buy", "sell")


# -- trade records -------------------------------------------------------


@dataclass(frozen=True)
class Trade:
    """One executed (or hypothetical) trade, as the costs module sees it.

    Conventions, stated so they can be argued with:

      - ``qty`` is UNSIGNED size; direction lives in ``side``.  A
        negative qty is a bug, and __post_init__ says so.
      - ``side == "sell"`` is treated as holding a SHORT position for
        ``holding_days`` - borrow is charged on sells only.  This model
        prices one-shot trades, not position timelines; a long round
        trip is buy-then-sell and pays borrow on neither leg.
      - ``ts`` is stored exactly as given (str or datetime); this module
        never parses it.  Persistence layers should pass ISO strings.
      - ``sigma_bps`` (daily volatility in bps) and ``adv`` (average
        daily volume, same units as qty) are optional context.  Without
        them the impact line comes back zero with explained=False -
        see ImpactModel.estimate for why that distinction matters.
    """

    ts: Union[str, datetime]
    symbol: str
    side: str
    price: float
    qty: float
    sigma_bps: Optional[float] = None
    adv: Optional[float] = None
    holding_days: float = 0.0

    def __post_init__(self) -> None:
        if self.side not in _SIDES:
            raise ValueError(f"side must be 'buy' or 'sell', got {self.side!r}")
        if not self.symbol:
            raise ValueError("symbol must be non-empty")
        if self.price <= 0:
            raise ValueError(f"price must be positive, got {self.price}")
        if self.qty < 0:
            raise ValueError(
                f"qty is unsigned size (direction lives in side), got {self.qty}"
            )
        if self.holding_days < 0:
            raise ValueError(f"holding_days must be >= 0, got {self.holding_days}")

    @property
    def notional(self) -> float:
        """price * qty - the money the trade moves, independent of side."""
        return abs(self.price) * abs(self.qty)


@dataclass(frozen=True)
class CostBreakdown:
    """The itemized reality tax on one trade, in monetary units.

    ``fee``, ``spread_cost``, ``impact_cost`` and ``borrow_cost`` are the
    four stored lines.  ``total`` and ``total_bps`` are DERIVED
    properties, not stored fields: a breakdown cannot disagree with its
    own parts.  ``notional`` is stored because total_bps is a rate and a
    breakdown without its base cannot state one.

    ``impact_explained`` is False when an impact model ran but lacked
    sigma/ADV.  Reports should then say "impact unestimated", not "0" -
    a silent zero is how impact quietly vanishes from a backtest.
    """

    fee: float = 0.0
    spread_cost: float = 0.0
    impact_cost: float = 0.0
    borrow_cost: float = 0.0
    notional: float = 0.0
    impact_explained: bool = True

    @property
    def total(self) -> float:
        """fee + spread + impact + borrow, in monetary units."""
        return self.fee + self.spread_cost + self.impact_cost + self.borrow_cost

    @property
    def total_bps(self) -> float:
        """total as basis points of notional (0.0 when notional is 0)."""
        if self.notional <= 0.0:
            return 0.0
        return self.total / self.notional * 1e4


# -- the four lines ------------------------------------------------------


class FeeSchedule:
    """Commission model.  Both lines add, so a broker charging bps AND
    per-share is expressible; zero() is the common fantasy."""

    def __init__(self, *, bps: float = 0.0, cents_per_share: float = 0.0) -> None:
        self.bps = float(bps)
        self.cents_per_share = float(cents_per_share)

    @classmethod
    def flat_bps(cls, bps: float) -> "FeeSchedule":
        """Basis points of notional per trade.  flat_bps(10) on $1M -> $1000."""
        return cls(bps=bps)

    @classmethod
    def per_share(cls, cents: float) -> "FeeSchedule":
        """Cents per share.  per_share(0.5) on 1000 shares -> $5.00."""
        return cls(cents_per_share=cents)

    @classmethod
    def zero(cls) -> "FeeSchedule":
        """The free lunch.  It is a lie, but a configurable one."""
        return cls()

    def apply(self, notional: float, shares: float) -> float:
        """Total commission, in the same monetary units as `notional`."""
        return notional * self.bps / 1e4 + shares * self.cents_per_share / 100.0


class SpreadModel:
    """The cost of crossing the spread.

    THE CONVENTION - READ THIS TWICE:

    A quote is bid/ask around a mid.  A market order does not trade at
    the mid; it CROSSES, paying the HALF-spread (ask-mid for a buy,
    mid-bid for a sell).  In this module one Trade record is one
    crossing and pays exactly one half-spread:

        cost = price * qty * (spread_bps / 2) / 1e4

    A round trip is TWO Trade records (entry and exit) and pays two
    half-spreads - i.e. exactly the full spread, once.  If your numbers
    show half a spread per round trip here, you have built a strategy
    that trades in a market with no other side.  Conversely: do not
    "correct" this to a full spread per trade, or you will pay the
    spread twice.

    Volatility scaling: real spreads widen when volatility spikes.
    volatility_scaled multiplies the base by (1 + mult * sigma_bps/1000)
    when the trade carries a sigma, and leaves the base untouched when
    it does not - an unestimated sigma is not a calm market, but we
    cannot charge for fear we cannot see.
    """

    def __init__(self, *, base_bps: float, mult: Optional[float] = None) -> None:
        self.base_bps = float(base_bps)
        self.mult = mult

    @classmethod
    def constant(cls, bps: float) -> "SpreadModel":
        """A spread that never moves.  Convenient; never true."""
        return cls(base_bps=bps)

    @classmethod
    def volatility_scaled(cls, base_bps: float, mult: float = 8.0) -> "SpreadModel":
        """spread_bps = base_bps * (1 + mult * sigma_bps/1000) when sigma known."""
        return cls(base_bps=base_bps, mult=mult)

    def spread_bps(self, sigma_bps: Optional[float] = None) -> float:
        """The full bid-ask spread in bps under this model."""
        if self.mult is None or sigma_bps is None:
            return self.base_bps
        return self.base_bps * (1.0 + self.mult * sigma_bps / 1000.0)

    def half_cost(
        self, price: float, qty: float, sigma_bps: Optional[float] = None
    ) -> float:
        """Cost in monetary units of ONE crossing at this spread.

        Worked example: constant(20), price=100, qty=10 -> half-spread
        is 10 bps = 0.001 of the $1,000 notional -> cost = 100 * 10 *
        0.001 = 1.00.

        The optional sigma_bps argument exists because volatility-scaled
        spreads need the trade's volatility; a two-argument call still
        works and means "sigma unknown".
        """
        half_bps = self.spread_bps(sigma_bps) / 2.0
        return price * qty * half_bps / 1e4


class ImpactModel:
    """Price impact - you are not the only one in the book.

    The square-root law (Bouchaud, Farmer & Lillo on order-flow
    impact; Almgren & Chriss 2000 for the execution-framework view):

        impact_bps = coeff * sigma_bps * sqrt(qty / adv)

    Impact grows with the square root of participation (qty/adv), is
    measured in bps of the trade's notional, and scales with the day's
    volatility - a violent market moves further when you push it.
    coeff=1.0 is a placeholder, not a law of nature; calibrate it
    against your own fills and stop trusting the default.

    The linear alternative (impact_bps = eta * participation, eta=0.1)
    is kept for comparison - it is what naive execution models assume
    and what the square-root law says is far too kind at size.

    If a required input is missing (ADV always; sigma for the sqrt law)
    the model returns impact 0.0 with explained=False: an unestimated
    impact is NOT a zero impact, and the flag exists so reports can say
    "impact unestimated - no sigma/ADV" out loud instead of silently
    booking a costless whale trade.
    """

    def __init__(self, *, kind: str = "sqrt", coeff: float = 1.0, eta: float = 0.1) -> None:
        if kind not in ("sqrt", "linear"):
            raise ValueError(f"kind must be 'sqrt' or 'linear', got {kind!r}")
        self.kind = kind
        self.coeff = float(coeff)
        self.eta = float(eta)

    @classmethod
    def sqrt_law(cls, coeff: float = 1.0) -> "ImpactModel":
        """impact_bps = coeff * sigma_bps * sqrt(qty / adv)."""
        return cls(kind="sqrt", coeff=coeff)

    @classmethod
    def linear(cls, eta: float = 0.1) -> "ImpactModel":
        """impact_bps = eta * (qty / adv).  Sigma-independent."""
        return cls(kind="linear", eta=eta)

    def estimate(
        self, qty: float, adv: Optional[float], sigma_bps: Optional[float]
    ) -> tuple[float, bool]:
        """Return (impact in bps of notional, explained).

        explained is False exactly when a required input was missing;
        impact 0.0 then means "could not estimate", never "no impact".
        With all inputs present the flag is True even when the formula
        legitimately evaluates to 0 (qty == 0 is a real zero, not a
        missing estimate).
        """
        if adv is None or adv <= 0.0:
            return 0.0, False
        participation = qty / adv
        if self.kind == "sqrt":
            if sigma_bps is None:
                return 0.0, False
            return self.coeff * sigma_bps * math.sqrt(participation), True
        return self.eta * participation, True

    def impact_bps(
        self, qty: float, adv: Optional[float], sigma_bps: Optional[float]
    ) -> float:
        """Convenience form of estimate() that discards the explained flag."""
        bps, _ = self.estimate(qty, adv, sigma_bps)
        return bps


class BorrowModel:
    """Stock borrow: shorts pay rent.

    cost = notional * annual_rate * holding_days / 365

    Simplifications, stated: one flat rate for every name (no
    hard-to-borrow tiers), calendar-day accrual over a 365-day year, no
    business-day nuance.  A sell is a short (see Trade); buys pay
    nothing.
    """

    def __init__(self, rate: float = 0.05) -> None:
        self.rate = float(rate)

    @classmethod
    def annual(cls, rate: float = 0.05) -> "BorrowModel":
        """A flat annual rate, prorated by holding period."""
        return cls(rate=rate)

    def cost(self, notional: float, holding_days: float) -> float:
        """Borrow charge for holding `notional` short for `holding_days`."""
        return notional * self.rate * holding_days / 365.0


# -- composition ---------------------------------------------------------


class CostModel:
    """The composed reality tax: fees + spread + impact + borrow.

    Every component left None is a cost line of zero.  The model will
    not remind you that you forgot impact - the impact_explained flag
    only fires when an impact model EXISTS but lacked sigma/ADV.  If you
    want the report to be able to warn, pass an impact model.
    """

    def __init__(
        self,
        fees: Optional[FeeSchedule] = None,
        spread: Optional[SpreadModel] = None,
        impact: Optional[ImpactModel] = None,
        borrow: Optional[BorrowModel] = None,
    ) -> None:
        self.fees = fees if fees is not None else FeeSchedule.zero()
        self.spread = spread if spread is not None else SpreadModel.constant(0.0)
        self.impact = impact
        self.borrow = borrow

    def price(self, trade: Trade) -> CostBreakdown:
        """Itemize the four cost lines for one trade."""
        qty = abs(trade.qty)
        notional = trade.notional
        fee = self.fees.apply(notional, qty)
        spread_cost = self.spread.half_cost(trade.price, qty, trade.sigma_bps)
        if self.impact is not None:
            impact_bps, explained = self.impact.estimate(qty, trade.adv, trade.sigma_bps)
            impact_cost = notional * impact_bps / 1e4
        else:
            impact_cost, explained = 0.0, True
        if self.borrow is not None and trade.side == "sell":
            borrow_cost = self.borrow.cost(notional, trade.holding_days)
        else:
            borrow_cost = 0.0
        return CostBreakdown(
            fee=fee,
            spread_cost=spread_cost,
            impact_cost=impact_cost,
            borrow_cost=borrow_cost,
            notional=notional,
            impact_explained=explained,
        )

    def apply(self, trades: Sequence[Trade]) -> list[CostBreakdown]:
        """Price every trade, in order."""
        return [self.price(t) for t in trades]

    def total_bps(self, trades: Sequence[Trade]) -> float:
        """Aggregate cost rate: total cost over total notional, in bps.

        Weighted by notional, not averaged - 1bp on $1M plus 100bps on
        $10 is 1.001bps, not 50.5.  Zero notional trades cost nothing
        and say so (0.0, not a division by zero).
        """
        total = 0.0
        notional = 0.0
        for breakdown in self.apply(trades):
            total += breakdown.total
            notional += breakdown.notional
        if notional <= 0.0:
            return 0.0
        return total / notional * 1e4


# -- the reality check ---------------------------------------------------


def fantasy_vs_real(
    returns_no_cost: Sequence[float],
    costs_bps: float,
    periods_per_year: int,
) -> dict:
    """Sharpe before costs, Sharpe after costs, and the shrinkage between.

    This is the function that turns "0.1% per trade is nothing" into a
    number.

    Simplification, stated: the per-period drag costs_bps/1e4 is
    subtracted from the MEAN per-period return while the standard
    deviation is left untouched - costs are modeled as a deterministic
    levy uncorrelated with returns.  Compounding and the dispersion of
    per-trade costs are second-order here; the first-order truth this
    function exists for is that a small per-trade cost applied at high
    turnover is not small.

    Sharpe convention: sample standard deviation (ddof=1), annualized by
    sqrt(periods_per_year) - the same convention forensics.stats uses.

    Returns a dict with keys:

      sharpe_before    annualized Sharpe of the costless fantasy
      sharpe_after     annualized Sharpe after the per-period drag
      shrinkage        sharpe_after / sharpe_before.  NaN when the
                       pre-cost Sharpe is exactly zero (the ratio is
                       undefined; the absolute after-cost Sharpe tells
                       the story).  Above 1.0 for a losing strategy:
                       costs hurt bad strategies too, and the ratio
                       says so honestly.
      drag_per_period  costs_bps / 1e4, quoted so reports can show it
      n_periods        len(returns_no_cost)

    Zero-variance returns have an undefined Sharpe; we return 0.0
    rather than pretend a constant series is an edge.
    """
    n = len(returns_no_cost)
    if n < 2:
        raise ValueError("need at least 2 returns to estimate a Sharpe")
    mean = statistics.fmean(returns_no_cost)
    std = statistics.stdev(returns_no_cost)
    drag = costs_bps / 1e4
    ann = math.sqrt(periods_per_year)
    if std > 0.0:
        before = (mean / std) * ann
        after = ((mean - drag) / std) * ann
    else:
        before = after = 0.0
    ratio = after / before if before != 0.0 else float("nan")
    return {
        "sharpe_before": before,
        "sharpe_after": after,
        "shrinkage": ratio,
        "drag_per_period": drag,
        "n_periods": n,
    }
