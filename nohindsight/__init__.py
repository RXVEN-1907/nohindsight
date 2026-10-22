"""nohindsight - a quant toolkit that answers one question:

    is your strategy real, or did you cheat by accident?

Five departments, one job:
    core       the two clocks (event_time vs knowledge_time) and the guard
    data       append-only, revisioned, point-in-time data ledgers
    checks     the police: scanners that catch dirty data and lookahead code
    costs      reality tax: fees, spread, impact, borrow
    forensics  is it luck?  deflated Sharpe, PBO, permutation tests
    live       paper trading with a kill switch and a fill emulator
"""

__version__ = "0.1.0"

from .core import AsOfKeyError, AsOfView, Clock, Entry, LeakError, Ledger, to_utc

__all__ = [
    "AsOfKeyError",
    "AsOfView",
    "Clock",
    "Entry",
    "LeakError",
    "Ledger",
    "to_utc",
    "__version__",
]
