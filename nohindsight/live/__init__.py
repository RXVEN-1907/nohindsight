"""nohindsight.live — paper trading with a kill switch and a fill emulator.

The live department is where a strategy meets a market that fights back:
fills arrive in pieces (but never lose size), some orders are refused, all
money is Decimal, and one hard rule sits between the strategy and its
money — blow the drawdown limit and the kill switch latches forever.
"""

from .orders import Fill, LimitOrder, MarketOrder, Refusal, to_decimal
from .fillsim import FillEmulator
from .risk import AccountState, RiskConfig, RiskGuard
from .broker import FlatFee, PaperBroker
from .replay import Ctx, Replay, ReplayResult

__all__ = [
    "AccountState",
    "Ctx",
    "Fill",
    "FillEmulator",
    "FlatFee",
    "LimitOrder",
    "MarketOrder",
    "PaperBroker",
    "Refusal",
    "Replay",
    "ReplayResult",
    "RiskConfig",
    "RiskGuard",
    "to_decimal",
]
