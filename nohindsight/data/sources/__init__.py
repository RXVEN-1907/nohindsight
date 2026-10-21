"""nohindsight.data.sources — network-shaped skeletons with offline-safe cores.

Every fetcher is a thin urllib wrapper that raises its source's single
failure type (EdgarUnavailable / FredUnavailable); every parser is a pure
function on text or payloads, so the tests never touch the network.
"""

from .edgar import (
    SEC_API,
    USER_AGENT,
    EdgarUnavailable,
    company_concept_url,
    facts_to_ledger,
    fetch_json,
)
from .fred import (
    ALFRED_CSV,
    FRED_API,
    FredUnavailable,
    alfred_vintage_url,
    fetch_text,
    fred_series_url,
    vintages_to_ledger,
)

__all__ = [
    "ALFRED_CSV",
    "FRED_API",
    "SEC_API",
    "USER_AGENT",
    "EdgarUnavailable",
    "FredUnavailable",
    "alfred_vintage_url",
    "company_concept_url",
    "facts_to_ledger",
    "fetch_json",
    "fetch_text",
    "fred_series_url",
    "vintages_to_ledger",
]
