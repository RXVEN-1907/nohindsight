"""nohindsight.costs — reality tax: fees, spread, impact, borrow.

The department that answers "your Sharpe is 2.0 — after what, exactly?".
Every component is priced separately so the report can name which line
ate the edge:

    FeeSchedule   commission (bps of notional and/or cents per share)
    SpreadModel   the half-spread paid per crossing (convention
                  documented loudly on the class)
    ImpactModel   the square-root law of market impact
    BorrowModel   rent on short positions, prorated by holding days
    CostModel     the four lines, composed, per trade
    fantasy_vs_real  Sharpe before costs vs after, and the shrinkage

Pure arithmetic on Trade records: no clock, no data access, no way to
peek at the future.  The dishonesty costs enable is subtraction, not
lookahead — see fantasy_vs_real.
"""

from .model import (
    BorrowModel,
    CostBreakdown,
    CostModel,
    FeeSchedule,
    ImpactModel,
    SpreadModel,
    Trade,
    fantasy_vs_real,
)

__all__ = [
    "BorrowModel",
    "CostBreakdown",
    "CostModel",
    "FeeSchedule",
    "ImpactModel",
    "SpreadModel",
    "Trade",
    "fantasy_vs_real",
]
