"""nohindsight.data.sources.fred - ALFRED vintages, the honest macro feed.

FRED shows a series as revised today.  ALFRED shows every vintage as each
revision was first published.  The vintage CSV endpoint needs no API key:

    https://alfred.stlouisfed.org/graph/alfred_graph.csv?id=UNRATE&cosd=2018-01-01

Its rows carry ``realtime_start`` - the day that vintage became public -
which is exactly a knowledge_time.  The plain FRED observations API (today's
fully revised series - the dishonest one, useful only for comparison) does
need a free key: https://fredaccount.stlouisfed.org/apikeys
"""

from __future__ import annotations

import csv
import io
import urllib.error
import urllib.request
from datetime import date, datetime

from ...core import Ledger, to_utc
from .edgar import USER_AGENT

__all__ = [
    "ALFRED_CSV",
    "FRED_API",
    "FredUnavailable",
    "alfred_vintage_url",
    "fetch_text",
    "fred_series_url",
    "vintages_to_ledger",
]

FRED_API = "https://api.stlouisfed.org"
ALFRED_CSV = "https://alfred.stlouisfed.org/graph/alfred_graph.csv"


class FredUnavailable(RuntimeError):
    """ALFRED/FRED could not be reached, or answered with something unusable."""


def _as_iso_date(value: str | date | datetime) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip()  # assumed "YYYY-MM-DD"; the API will say otherwise


def alfred_vintage_url(
    series_id: str, date: str | date | datetime, api_key: str | None = None
) -> str:
    """Vintage CSV for `series_id` from `date` forward.  No key needed."""
    url = f"{ALFRED_CSV}?id={series_id}&cosd={_as_iso_date(date)}"
    if api_key is not None:
        url += f"&api_key={api_key}"
    return url


def fred_series_url(series_id: str, api_key: str | None = None) -> str:
    """Today's fully revised series - the one that quietly rewrites history."""
    url = f"{FRED_API}/series/observations?series_id={series_id}&file_type=json"
    if api_key is not None:
        url += f"&api_key={api_key}"
    return url


def fetch_text(url: str, user_agent: str = USER_AGENT, timeout: float = 30.0) -> str:
    """GET `url` as text; every failure becomes FredUnavailable."""
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise FredUnavailable(f"could not fetch {url} ({exc})") from exc


def vintages_to_ledger(csv_text: str, series_id: str) -> Ledger:
    """Parse an ALFRED vintage CSV into a Ledger.  Pure, no network.

    Columns: realtime_start, realtime_end, date, and one value column named
    after the series.  Per row:
        knowledge_time = realtime_start - when that vintage was published
        event_time     = date           - the observation being described
        value          = the series column, as float
    The same date appearing under many realtime_starts is the point: those
    are the vintages, and the Ledger keeps every one of them.  FRED marks a
    missing observation with "." - those rows are skipped.
    """
    ledger = Ledger(f"fred.{series_id}")
    reader = csv.DictReader(io.StringIO(csv_text))
    fields = reader.fieldnames or []
    for col in ("realtime_start", "date", series_id):
        if col not in fields:
            raise ValueError(f"ALFRED CSV missing column {col!r}; header has {fields}")
    for row in reader:
        raw = (row.get(series_id) or "").strip()
        if raw in ("", "."):
            continue  # FRED's missing-value marker: no datum, no revision
        ledger.append(
            series_id,
            float(raw),
            (row.get("date") or "").strip(),
            (row.get("realtime_start") or "").strip(),
        )
    return ledger
