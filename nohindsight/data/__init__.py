"""nohindsight.data - append-only, revisioned, point-in-time ledgers.

core.py defines what an honest datum is (event_time, knowledge_time, and a
guard that refuses the future).  This package makes such data: synthetic
worlds with crimes planted for the demo, CSV ingest, the survivorship
universe, persistence, and offline-safe source skeletons for SEC/EDGAR and
FRED/ALFRED.
"""

from ..core import Ledger
from .persisted import load_ledger, save_ledger
from .sources.edgar import (
    SEC_API,
    USER_AGENT,
    EdgarUnavailable,
    company_concept_url,
    facts_to_ledger,
    fetch_json,
)
from .sources.fred import (
    ALFRED_CSV,
    FRED_API,
    FredUnavailable,
    alfred_vintage_url,
    fetch_text,
    fred_series_url,
    vintages_to_ledger,
)
from .synthetic import (
    synthetic_fundamentals,
    synthetic_macro,
    synthetic_prices,
    synthetic_trades,
)
from .tabular import ingest_csv
from .universe import Membership, Universe

__all__ = [
    "ALFRED_CSV",
    "FRED_API",
    "Ledger",
    "Membership",
    "SEC_API",
    "USER_AGENT",
    "Universe",
    "EdgarUnavailable",
    "FredUnavailable",
    "alfred_vintage_url",
    "company_concept_url",
    "facts_to_ledger",
    "fetch_json",
    "fetch_text",
    "fred_series_url",
    "ingest_csv",
    "load_ledger",
    "save_ledger",
    "synthetic_fundamentals",
    "synthetic_macro",
    "synthetic_prices",
    "synthetic_trades",
    "vintages_to_ledger",
]
