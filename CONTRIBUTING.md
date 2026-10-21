# contributing

The bar for a feature is one question: **does it help answer "is this
backtest real, or is it lying?"** If not, it belongs in another repo.

Rules:
- Pure Python stdlib in `nohindsight/` — no third-party runtime deps.
- Every check reports findings, never mutates data. The police don't
  tamper with evidence.
- Tests: `python -m unittest discover -s tests -v` (pytest works too).
  Deterministic — seed everything.
- Crimson is only ever for leaks. Lavender is only ever for truth.
  See `docs/DESIGN.md`.
- Docstrings state assumptions, especially simplifications. An honest
  library about honesty should document its own shortcuts.
