# Runbook for coding agents

Instructions for Claude Code (or any agent that reads repo-level instructions) working in
this repository. Written for the job people actually ask for: **refresh the dashboard.**

## What this repo is

A local, offline-first terminal over Robinhood Chain (chain ID 4663): every tokenized stock,
every pool quoting a memecoin against one, and which are trading. `dashboard.html` at the repo
root is the product — a single self-contained file with the data inlined, opened directly off
disk via `file://`.

There is no server, no hosted site, no build step, no dependencies. **Python 3 standard
library only.** Do not add a `requirements.txt`, a virtualenv, a package manager, or a
bundler. If you think you need a library, you have misread the problem.

## Refreshing the dashboard

This is the common request. Run from the repo root.

### If `data/` already has state (the fast path, seconds to ~10 min)

```bash
python3 pipeline/live.py --window 6h   # which pools traded recently
python3 pipeline/collect.py            # price them via GeckoTerminal
python3 pipeline/render.py             # -> dashboard.html
```

Then tell the user to reload `dashboard.html` in their browser.

### If `data/` is empty or missing (first run after a clone)

`data/` is gitignored, so a fresh clone has no state. Run the full chain in order — each stage
writes what the next one reads:

```bash
python3 pipeline/registry.py           # 203 stock tokens from the factory
python3 pipeline/pools.py              # every stock-paired pool, from genesis
python3 pipeline/symbols.py            # ticker/name per counterparty + impostor dating
python3 pipeline/live.py --window 6h
python3 pipeline/collect.py
python3 pipeline/render.py
```

### Expected timings — do not assume a hang

> **A cold start takes about an hour.** Measured on a fresh clone: `pools.py` 27 min,
> `symbols.py` 17 min, plus the liveness sweep and pricing. It is not hung — those two stages
> sweep the whole chain and print little while they work. Every later refresh resumes from a
> cursor and takes minutes. If you only want current prices on pools already known, use
> `./refresh.sh --quick`, which skips both.

| Stage | Cold | Warm | Notes |
|---|---|---|---|
| `registry.py` | ~20 s | ~20 s | one `eth_getLogs` against the factory; measured 21 s |
| `pools.py` | **~27 min** | seconds | sweeps from genesis once, then resumes from a cursor |
| `symbols.py` | **~17 min** | fast | checkpoints progress; safe to re-run after an interrupt |
| `live.py --window 6h` | ~1–3 min | same | no cache; always re-sweeps the window |
| `collect.py` | ~5–10 min | same | **the wall-clock cost**; self-paced to ~23 req/min |
| `render.py` | <1 s | <1 s | measured 0.25 s for a 2.57 MB output |

`collect.py` is slow **by design**. GeckoTerminal's free tier is ~30 calls/min; the collector
paces to ~23/min because 27/min was measured tripping a 429 mid-run. Do not "optimise" this by
removing the sleep — an earlier version fetched without checking HTTP status, could not see the
429, and burned 25 minutes producing nothing.

**Never run these in parallel.** Every stage reads the previous stage's file, and they share
one rate-limit budget against the same two hosts.

## Verifying you are done

```bash
python3 pipeline/verify.py
```

It exits non-zero on failure. With full state, 27 checks pass. On a fresh clone with no
`data/`, 10 pass (registry from `fixtures/stocks.json` + the dashboard checks) and it exits 0 —
that is expected, not a failure.

Also confirm `dashboard.html`'s mtime actually changed. `render.py` prints the byte size and
the token-index count; a plausible result is ~2.5 MB and ~27,000 tokens.

## Invariants — do not break these

1. **Never use vendor per-token pair listings for discovery.** DexScreener caps at 30 pools
   and GeckoTerminal at 20, neither sorted by liquidity, so they silently drop live pools —
   together they return 36 of AAPL's 1,600+. Pools come from chain-wide pool-creation events;
   vendors are a **pricing service addressed by pool id** (per-pool lookups are uncapped).
   `verify.py` asserts GUH/AAPL, CLARUS/AAPL and PINE/AAPL are present specifically to catch
   this regression. If that test fails, discovery has regressed — fix it, do not delete it.

2. **The dashboard must inline its data, never `fetch()` it.** A `file://` page cannot fetch a
   sibling JSON under CORS. This is the entire reason `render.py` is a separate stage from
   `collect.py`. `verify.py` asserts `fetch(` does not appear in the HTML.

3. **Never spoof a browser `User-Agent`, and never use Blockscout.** It sits behind Cloudflare
   and only returns JSON to a spoofed UA. That is scraping. Everything it offered has a native
   RPC equivalent. `verify.py` scans every module in `pipeline/` for the header and fails.

4. **Never commit `data/`.** `pools.json` alone is 20 MB and goes stale within minutes.
   `.gitignore` blocks it; keep it that way. `dashboard.html` *is* committed — it is the
   artifact, with the snapshot inlined.

5. **Escape token-derived strings before they reach `innerHTML`.** Token names are
   attacker-controlled; one on this chain is literally named `</script><b>pwn`.

6. **A missing value renders `—`, never `0`.** A pool younger than 4h has no 4h number, and
   unindexed V4 liquidity is unknown, not zero. Do not coerce these to zero.

## Failure modes you will actually hit

| Symptom | Cause | Do this |
|---|---|---|
| HTTP 429 from the RPC | sustained querying, or a batch >25 sub-requests | back off and retry the **same** range; `chain.batch()` already chunks to 25. Do not bisect a 429 — it makes it worse |
| `eth_getLogs` returns a cap/timeout error | >10,000 matched logs in the range | bisect the block range (`chain.sweep()` does this) |
| `pools.py` warns about unscanned ranges | some sweeps failed | the count is a lower bound; re-run `python3 pipeline/pools.py --retry-gaps` |
| `metadata is not found` from `eth_getCode` | the public RPC is not an archive node | date contracts by first `Transfer` log instead; do not binary-search for a deploy block |
| `collect.py` produces nothing after a long run | GeckoTerminal 429 | it is paced already; wait and re-run, do not raise the rate |
| `render.py` says "run pipeline/collect.py first" | no `data/snapshot.json` | run the earlier stages in order |

## Traps that produce silently wrong numbers

- **Pool orientation flips.** GeckoTerminal's `price_change_percentage` describes the pool's
  BASE token, and many pools have the stock as base. Inverting is `1/(1+r)-1` — **not**
  negation. Getting this wrong reports the sign backwards with no error.
- **A pool's `market_cap_usd` is the base token's cap.** The AAPL/USDG pool reports $3.33e9;
  that is USDG, not AAPL. Caps come from the token endpoint or `totalSupply × price`.
- **Corrupt vendor reserves exist.** One pool with $25k FDV reported `reserve_in_usd = 2.6e49`.
  Implausible reserves are discarded, not displayed.
- **Block rate is non-uniform** (~4/s early, ~10/s now). Never convert blocks↔time with a
  constant; use `chain.block_at_time()`, which binary-searches real timestamps.
- **Pool dates older than ~24h are interpolated** (`stride=8`) and flagged `created_approx`.
  Anything within ~24h of head is exact.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `RH_RPC` | `https://rpc.mainnet.chain.robinhood.com` | RPC endpoint |
| `RH_DATA` | `<repo>/data` | pipeline state directory |
| `RH_PACE` | `0.15` | seconds between RPC requests; pacing beats backing off |

## Scope

If asked to add live in-browser price polling or publish this as a hosted site, see
`BUILD-PLAN.md` — that path is designed but deliberately **not** built. This repo is
intentionally local-only. Do not add analytics, telemetry, or any outbound call from the page
beyond the fonts and the DexScreener links it already has.
