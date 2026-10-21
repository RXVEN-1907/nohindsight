"""nohindsight.data.persisted — ledgers that survive the process.

JSONL, one Entry per line:

    {"key", "value", "event_time", "knowledge_time"}

datetimes as ``.isoformat()`` strings.  Writes go to ``<path>.tmp`` and then
``os.replace``, so a reader never catches a half-written ledger and a crash
mid-write never corrupts the previous one.  Values must be JSON-friendly;
anything else fails loudly at save time rather than silently at load time.
"""

from __future__ import annotations

import json
import os

from ..core import Ledger

__all__ = ["save_ledger", "load_ledger"]


def save_ledger(ledger: Ledger, path: str) -> int:
    """Write every entry of `ledger` to `path` as JSONL; return the count.

    The file is replaced whole (tmp + os.replace) — append-safe in the
    sense that a partial write is never visible under `path`.
    """
    entries = ledger.entries()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for e in entries:
            fh.write(
                json.dumps(
                    {
                        "key": e.key,
                        "value": e.value,
                        "event_time": e.event_time.isoformat(),
                        "knowledge_time": e.knowledge_time.isoformat(),
                    }
                )
                + "\n"
            )
    os.replace(tmp, path)
    return len(entries)


def load_ledger(path: str, name: str = "loaded") -> Ledger:
    """Replay a saved JSONL into a fresh Ledger named `name`.

    Revisions reconstruct themselves from append order — the file is
    written in the ledger's own entry order, so replaying it reproduces
    both the revision numbers and the per-key knowledge ordering.  Blank
    lines are skipped; any other malformed line raises ValueError naming
    the line number (1-based, counting every line of the file).
    """
    ledger = Ledger(name)
    with open(path, "r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                rec = json.loads(text)
                if not isinstance(rec, dict):
                    raise TypeError("expected a JSON object per line")
                ledger.append(
                    rec["key"],
                    rec["value"],
                    rec["event_time"],
                    rec["knowledge_time"],
                )
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                raise ValueError(
                    f"{path}: line {lineno}: malformed ledger record ({exc})"
                ) from exc
    return ledger
