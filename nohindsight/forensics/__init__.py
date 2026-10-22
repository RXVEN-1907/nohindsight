"""nohindsight.forensics - is it luck?

The police in `checks` catch a dirty backtest; this package interrogates a
CLEAN one.  Four questions, four files:

    stats    the five classical numbers (sharpe, sortino, drawdown, calmar, t)
    deflate  how much of that Sharpe is just having tried many times
    pbo      how often the in-sample winner stays a winner out-of-sample
    audit    the verdict, with the reasons in plain language

Everything operates on plain per-period return lists - anything that can
produce those (a Ledger-driven backtest, a CSV, live fills) can be
interrogated here.  Pure stdlib; deterministic given seeds.
"""

from .audit import REAL, FANTASY, SUSPICIOUS, Verdict, audit
from .deflate import deflated_psr, expected_max_sharpe, moments, psr
from .pbo import overfit_report, pbo
from .stats import calmar, max_drawdown, sharpe, sortino, t_stat

__all__ = [
    "REAL",
    "FANTASY",
    "SUSPICIOUS",
    "Verdict",
    "audit",
    "deflated_psr",
    "expected_max_sharpe",
    "moments",
    "psr",
    "overfit_report",
    "pbo",
    "calmar",
    "max_drawdown",
    "sharpe",
    "sortino",
    "t_stat",
]
