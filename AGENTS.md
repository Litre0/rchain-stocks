# AGENTS.md

Full runbook: **[`CLAUDE.md`](CLAUDE.md)** — read it before changing anything. It covers the
refresh procedure, expected timings, failure modes, and the invariants that must not be broken.

Quick version — refresh the dashboard, from the repo root:

```bash
# warm (data/ already populated)
python3 pipeline/live.py --window 6h && python3 pipeline/collect.py && python3 pipeline/render.py

# cold (fresh clone; data/ is gitignored so it starts empty)
python3 pipeline/registry.py && python3 pipeline/pools.py && python3 pipeline/symbols.py && \
python3 pipeline/live.py --window 6h && python3 pipeline/collect.py && python3 pipeline/render.py

# check
python3 pipeline/verify.py
```

Python 3 standard library only — no dependencies, no build step, no server.

**Refreshing is slow.** Measured: `pools.py` ~27 min and `symbols.py` ~17 min (cold only, they
checkpoint), and `live.py` **~46 min for the default 6h window on every run** — it keeps no
cursor and re-sweeps. Cost is linear in the window, so a routine refresh is
`./refresh.sh --window 1h` (~10 min all in); `--window 24h` is ~3 hours. `--quick` skips
discovery but still sweeps. Nothing is hung when quiet. Never run stages in parallel. Never
commit `data/`.
