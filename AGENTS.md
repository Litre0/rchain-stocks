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

Python 3 standard library only — no dependencies, no build step, no server. `collect.py` takes
5–10 minutes and is rate-limit paced on purpose; it is not hung. Never run the stages in
parallel. Never commit `data/`.
