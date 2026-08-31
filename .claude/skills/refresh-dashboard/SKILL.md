---
name: refresh-dashboard
description: Use when working in the rchain-stocks repo and the user wants fresher data or wants to query it — "refresh the dashboard", "update the data", "the numbers are stale", "rebuild it", "what's trading against TSLA", "which memecoins are launching on stock pairs right now", "is this ticker the real one". Rebuilds dashboard.html from Robinhood Chain, and answers questions directly from the snapshot without a browser.
---

# Refresh the Robinhood Chain stock/meme dashboard

## Overview

This repo builds `dashboard.html` — a single self-contained file listing every tokenized
stock on Robinhood Chain (chain ID 4663), every pool quoting a memecoin against one, and
which are actually trading. It is opened straight off disk over `file://`.

**The dashboard is a snapshot, not a live feed.** The committed one was generated whenever it
was last rendered; refreshing means re-running the pipeline against the chain.

Python 3 standard library only. **No API keys, no accounts, no install, no network service.**
If you think you need a dependency, you have misread the problem.

## When to use

- "Refresh / update / rebuild the dashboard", "the data is stale", "get the latest"
- Questions about what is trading, which memecoins are paired with which stock, or whether a
  ticker is the real one — you can answer these from `data/snapshot.json` directly (see
  [Answering questions without the browser](#answering-questions-without-the-browser))

**Not for:** trading advice, or anything that requires a hosted service. This repo is
deliberately local-only.

## Step 1 — check how stale it actually is

Do this before refreshing; the user may not need a full run.

```bash
python3 -c "import json;print(json.load(open('data/snapshot.json'))['generated'])" 2>/dev/null \
  || echo "no snapshot: this is a fresh clone (cold start)"
```

On a fresh clone `data/` does not exist — it is gitignored, because `pools.json` alone is
20 MB and goes stale within minutes. The committed `dashboard.html` still opens and shows the
snapshot it was rendered with. Only the raw state is missing.

## Step 2 — refresh

One command. It detects cold vs warm and runs the right stages in dependency order:

```bash
./refresh.sh                # normal: pick up new pools, re-price, re-render
./refresh.sh --quick        # prices only; skips pool/ticker discovery (fastest)
./refresh.sh --full         # re-sweep pool discovery from genesis
./refresh.sh --window 24h   # widen the liveness window (default 6h)
```

Use `--quick` when the user just wants current prices on pools already known. Use the default
when they want **new launches** to appear — that is what the discovery stages are for.

If you need to drive the stages by hand, the order is fixed, because each reads what the
previous wrote:

```
registry.py -> pools.py -> symbols.py -> live.py --window 6h -> collect.py -> render.py
```

**Never run stages in parallel.** They chain through files and share one rate-limit budget
against the same two hosts.

### Expected timings — do not mistake slow for hung

| Stage | Cold | Warm | Notes |
|---|---|---|---|
| `registry.py` | ~20 s | ~20 s | one `eth_getLogs` against the factory; measured 21 s |
| `pools.py` | several minutes | seconds | sweeps from genesis once, then resumes from a cursor |
| `symbols.py` | many minutes | fast | checkpointed; safe to re-run after an interrupt |
| `live.py --window 6h` | ~1–3 min | same | no cache; always re-sweeps the window |
| `collect.py` | ~5–10 min | same | **the wall-clock cost**; self-paced to ~23 req/min |
| `render.py` | <1 s | <1 s | measured 0.25 s for 2.57 MB out |

`collect.py` is slow **on purpose**. GeckoTerminal's free tier is ~30 calls/min; 27/min was
measured tripping a 429 mid-run, so it paces to ~23. Do not remove the sleep to "speed it up" —
an earlier version fetched without checking HTTP status, could not see the 429, and burned
25 minutes producing nothing.

## Step 3 — confirm and report

`refresh.sh` runs this for you, but if driving by hand:

```bash
python3 pipeline/verify.py
```

Exits non-zero on failure. **27 checks pass** with full state. On a fresh clone with no
`data/`, **10 pass and it exits 0** — registry assertions come from `fixtures/stocks.json`
plus the dashboard checks. That is expected, not a failure.

Then tell the user to **reload `dashboard.html` in their browser** — it is a static file, so
an open tab keeps showing the old render until refreshed. Give them the `file://` path.

Report what changed: the new `generated` timestamp, and how many pools are live now.

## Answering questions without the browser

`data/snapshot.json` (present after a refresh) answers most questions directly — faster and
more precise than telling the user to squint at a table.

```
generated    ISO timestamp of the render
window       liveness window, e.g. "6h"
provenance   {stocks_total, pools_total, meme_pools_total, live_total, impostors, ...}
stocks[]     address, symbol, name, deployed, deployed_ts, pools, best_pool, best_liq
pairs[]      the rendered rows (see fields below)
tokens{}     address -> {address, symbol, registry, first_pool_block}   ~27k entries
quote_meta[] per-stock rollup: {symbol, pools, swaps, newest}
```

A `pairs[]` row:

| Field | Meaning |
|---|---|
| `pool`, `kind`, `dex` | pool id, `v2`/`v3`/`v4`, launchpad/dex name |
| `stock`, `stock_symbol` | the tokenized stock leg |
| `other`, `symbol`, `name` | the counterparty token (the memecoin, when `meme` is true) |
| `meme` | true when the non-stock leg is a memecoin, not a quote asset |
| `registry_other` | true if the counterparty is itself a registry stock |
| `created`, `created_ts` | pool creation; older than ~24h may be interpolated |
| `swaps` | swaps in the liveness window |
| `liquidity_usd`, `liq_rejected` | `liq_rejected` marks an implausible vendor reserve |
| `price_usd`, `fdv_usd` | price and fully diluted value |
| `volume_1h`, `volume_24h`, `txns_24h` | `txns_24h` is `[buys, sells]` |
| `chg` | `{m5, m15, m30, h1, h4, h6, h24}` percent; **missing key = no data, not zero** |

Example — the busiest memecoins paired with a given stock:

```bash
python3 - <<'EOF'
import json
d = json.load(open("data/snapshot.json"))
rows = [p for p in d["pairs"] if p["meme"] and p["stock_symbol"] == "TSLA"]
for p in sorted(rows, key=lambda r: r["swaps"], reverse=True)[:10]:
    print(f'{p["symbol"]:<14} swaps={p["swaps"]:<6} liq=${p.get("liquidity_usd") or 0:,.0f}')
EOF
```

Is a ticker the real one? **Registry membership is the proof; age only corroborates it.**

```bash
python3 - <<'EOF'
import json
d = json.load(open("data/snapshot.json"))
hits = [t for t in d["tokens"].values() if (t.get("symbol") or "").upper() == "GME"]
print(f'{len(hits)} tokens claim GME; {sum(1 for t in hits if t["registry"])} in the registry')
EOF
```

**Do not read `dashboard.html` to answer data questions** — it is 2.5 MB with the JSON inlined
and will flood your context. Read `data/snapshot.json`, or a slice of it, instead.

## Invariants — do not break these

1. **Never use vendor per-token pair listings for discovery.** DexScreener caps at 30 pools and
   GeckoTerminal at 20, neither sorted by liquidity, so they silently drop live pools —
   together they return 36 of AAPL's 1,600+. Pools come from chain-wide pool-creation events;
   vendors are only a **pricing service addressed by pool id** (per-pool lookups are uncapped).
   `verify.py` asserts GUH/AAPL, CLARUS/AAPL and PINE/AAPL are present to catch exactly this
   regression. If it fails, discovery has regressed — fix it, do not delete the test.
2. **The dashboard must inline its data, never `fetch()` it.** A `file://` page cannot fetch a
   sibling JSON under CORS. This is why `render.py` is a separate stage from `collect.py`.
3. **Never spoof a browser `User-Agent`, and never use Blockscout.** It sits behind Cloudflare
   and only serves JSON to a spoofed UA. That is scraping. `verify.py` scans `pipeline/` for
   the header and fails the run.
4. **Never commit `data/`.** It is gitignored. `dashboard.html` *is* committed — it is the
   artifact people clone the repo for.
5. **Escape token-derived strings before they reach `innerHTML`.** Token names are
   attacker-controlled; one on this chain is literally named `</script><b>pwn`.
6. **A missing value renders `—`, never `0`.** A pool younger than 4h has no 4h number;
   unindexed V4 liquidity is unknown, not zero. Never coerce these to zero.

## Failure modes

| Symptom | Cause | Do this |
|---|---|---|
| HTTP 429 from the RPC | sustained querying, or a batch >25 sub-requests | back off, retry the **same** range. `chain.batch()` already chunks to 25. Do not bisect a 429 — it makes it worse |
| `eth_getLogs` cap/timeout | >10,000 matched logs in the range | bisect the range; `chain.sweep()` does this |
| `pools.py` warns of unscanned ranges | some sweeps failed | count is a lower bound: `python3 pipeline/pools.py --retry-gaps` |
| `metadata is not found` from `eth_getCode` | public RPC is not an archive node | date contracts by first `Transfer` log; do not binary-search a deploy block |
| `collect.py` long run, no output | GeckoTerminal 429 | wait, re-run. Do not raise the rate |
| `render.py`: "run pipeline/collect.py first" | no `data/snapshot.json` | run the earlier stages in order |

Nothing here corrupts state on failure: `pools.py` and `symbols.py` checkpoint, so re-running
resumes rather than restarting.

## Traps that produce silently wrong numbers

- **Pool orientation flips.** GeckoTerminal's `price_change_percentage` describes the pool's
  BASE token, and many pools have the stock as base. Inverting is `1/(1+r)-1` — **not**
  negation. Getting this wrong reports the sign backwards with no error.
- **A pool's `market_cap_usd` is the base token's cap.** The AAPL/USDG pool reports $3.33e9 —
  that is USDG. Caps come from the token endpoint or `totalSupply × price`.
- **Corrupt vendor reserves exist.** A pool with $25k FDV reported `reserve_in_usd = 2.6e49`.
- **Block rate is non-uniform** (~4/s early, ~10/s now). Never convert blocks↔time with a
  constant; use `chain.block_at_time()`.

## Configuration

| Env var | Default | Purpose |
|---|---|---|
| `RH_RPC` | `https://rpc.mainnet.chain.robinhood.com` | RPC endpoint |
| `RH_DATA` | `<repo>/data` | pipeline state directory |
| `RH_PACE` | `0.15` | seconds between RPC requests; pacing beats backing off |
