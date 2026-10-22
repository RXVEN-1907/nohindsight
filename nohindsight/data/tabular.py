"""nohindsight.data.tabular - CSV in, honest ledger out.

A CSV is a pile of strings; this module turns it into revisions with two
clocks.  It trusts the header row and nothing else: timestamps go through
core.to_utc, values are floats when they can be parsed as floats and stay
strings when they can't - CSV has no types, and pretending otherwise is
how "N/A" becomes a price.
"""

from __future__ import annotations

import csv

from ..core import Ledger, to_utc

__all__ = ["ingest_csv"]


def _coerce(text: str | None) -> float | str:
    """Float when possible, else the string itself (None -> empty string)."""
    if text is None:
        return ""
    try:
        return float(text)
    except ValueError:
        return text


def ingest_csv(
    path: str,
    key_col: str,
    event_col: str,
    knowledge_col: str,
    value_col: str | None = None,
    value_cols: list[str] | None = None,
    name: str = "ingested",
) -> Ledger:
    """Ingest a header-row CSV into a Ledger.

    `key_col`, `event_col`, `knowledge_col` name the three required
    columns; timestamps may be any ISO-8601 text core.to_utc accepts.

    Value resolution - exactly one of these shapes:
      * value_cols (a list) -> dict value {col: coerced}
      * value_col           -> a single coerced value
      * neither given       -> every remaining column becomes the dict

    Raises ValueError if a required column is missing, if a timestamp
    fails to parse, or on the first row whose knowledge_time precedes its
    event_time - the message names the file line number.
    """
    ledger = Ledger(name)
    with open(path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        fields = reader.fieldnames or []
        missing = [c for c in (key_col, event_col, knowledge_col) if c not in fields]
        if missing:
            raise ValueError(
                f"{path}: missing column(s) {missing}; header has {fields}"
            )
        if value_cols is not None:
            missing = [c for c in value_cols if c not in fields]
            if missing:
                raise ValueError(
                    f"{path}: value_cols missing column(s) {missing}; header has {fields}"
                )
        extra_cols = [c for c in fields if c not in (key_col, event_col, knowledge_col)]
        for row in reader:
            lineno = reader.line_num
            key = (row.get(key_col) or "").strip()
            if not key:
                raise ValueError(f"{path}: line {lineno}: empty {key_col!r}")
            try:
                et = to_utc((row.get(event_col) or "").strip())
                kt = to_utc((row.get(knowledge_col) or "").strip())
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{path}: line {lineno}: bad timestamp ({exc})") from exc
            if kt < et:
                raise ValueError(
                    f"{path}: line {lineno}: knowledge_time {kt.isoformat()} precedes "
                    f"event_time {et.isoformat()} - nobody knows things before "
                    "they happen"
                )
            if value_cols is not None:
                value: float | str | dict[str, float | str] = {
                    c: _coerce(row.get(c)) for c in value_cols
                }
            elif value_col is not None:
                value = _coerce(row.get(value_col))
            else:
                value = {c: _coerce(row.get(c)) for c in extra_cols}
            ledger.append(key, value, et, kt)
    return ledger
