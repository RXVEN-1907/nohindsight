# contributing

A feature belongs here if it helps answer: is this backtest real, or is it
lying? If it does not, put it in another repo.

Rules:

- Pure Python stdlib in `nohindsight/`. No third-party runtime deps.
- Rust crate `nh` is std-only too. No unsafe.
- Checks report findings. They never mutate the data they scan.
- Tests: `python -m unittest discover -s tests -v` (pytest works too).
  Deterministic: seed everything.
- Colors: leaks are crimson `#E5484D`, honest series are lavender
  `#D0C4DF`. See `docs/DESIGN.md`.
- Docstrings state assumptions and simplifications. Readers will ask why;
  answer them in the file.
