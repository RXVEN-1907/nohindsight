"""nohindsight.data.sources.edgar - SEC XBRL company facts, point-in-time.

SEC requires a User-Agent header on every request and answers 403 without
one (see https://www.sec.gov/os/accessing-edgar-data).  The default UA
below names this library; pass your own with a contact address if you pull
seriously - they do rate-limit.

`fetch_json` is the only network call, and every failure it can meet comes
back as one exception type.  `facts_to_ledger` is a pure function on the
payload, so the test suite never touches the wire.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from ...core import Ledger

__all__ = [
    "SEC_API",
    "USER_AGENT",
    "EdgarUnavailable",
    "company_concept_url",
    "fetch_json",
    "facts_to_ledger",
]

SEC_API = "https://data.sec.gov"
USER_AGENT = "nohindsight/0.1 (github.com/RXVEN-1907/nohindsight)"


class EdgarUnavailable(RuntimeError):
    """The SEC could not be reached, or answered with something unusable."""


def company_concept_url(cik: int, taxonomy: str, tag: str, unit: str) -> str:
    """URL for one concept of one company, CIK zero-padded to 10 digits.

    Example - cik=320193, taxonomy="us-gaap", tag="Revenues", unit="USD":

        https://data.sec.gov/api/xbrl/companyconcept/CIK0000320193/us-gaap/Revenues/USD.json
    """
    return f"{SEC_API}/api/xbrl/companyconcept/CIK{int(cik):010d}/{taxonomy}/{tag}/{unit}.json"


def fetch_json(url: str, user_agent: str = USER_AGENT) -> dict:
    """GET `url` with the mandatory UA header; return the parsed JSON.

    Raises EdgarUnavailable on network failure, HTTP error, or a body that
    will not parse - callers catch one exception and tell the user the
    source is down, not that their strategy is.
    """
    req = urllib.request.Request(url, headers={"User-Agent": user_agent})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise EdgarUnavailable(
            f"could not fetch {url} ({exc}).  SEC rejects requests without a "
            f"User-Agent header; this library sends {USER_AGENT!r} by default."
        ) from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise EdgarUnavailable(f"{url} answered with a non-JSON body ({exc})") from exc


def facts_to_ledger(payload: dict, tag: str) -> Ledger:
    """Flatten a company-concept payload into a Ledger.  Pure, no network.

    Mapping, per fact (each carries end, filed, val, fy, fp, sometimes
    frame):
        key            "<cik>.<tag>"
        event_time     end    - the period the number describes
        knowledge_time filed  - when the world could first see it
        value          val
    Multiple filings for the same period arrive as multiple ledger
    revisions: restatements modeled natively, no dedup, no last-one-wins.
    Facts missing end/filed/val are skipped - SEC occasionally emits them.
    """
    cik = int(payload.get("cik", 0))
    ledger = Ledger(f"edgar.{cik}.{tag}")
    key = f"{cik}.{tag}"
    for facts in payload.get("units", {}).values():
        for fact in facts:
            end, filed, val = fact.get("end"), fact.get("filed"), fact.get("val")
            if end is None or filed is None or val is None:
                continue
            ledger.append(key, val, end, filed)
    return ledger
