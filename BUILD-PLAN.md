# Build plan — live public site

Tasks are deliberately small, independently verifiable and **resumable**: no task depends on
unsaved state from another, so a session that dies mid-way restarts at a known-good boundary
rather than losing the run.

**Ground truth is already in this repo.** `pipeline/` works and is verified. Do not rewrite it,
and do not change how discovery works — see "The one rule" at the bottom.

---

## Task 1 — Export slim client artifacts  ✳ start here

Write `build/export.py`, reading the pipeline's local state and emitting only what the browser
needs. Raw state is gitignored because `pools.json` is 20 MB.

| Output | Contents | Budget |
|---|---|---|
| `data/stocks.json` | 203 rows: address, symbol, name, deployed, deployed_ts | ~40 KB |
| `data/memes.json` | live meme×stock pools: pool id, meme address + symbol, quote symbol + address, created_ts, swaps | ~250 KB |
| `data/quote_meta.json` | per-stock rollup: swaps, pool count, newest launch | ~5 KB |
| `data/tickers.json` | 27k rows: address, symbol, name, registry flag, first-seen, deployer | ~1.5 MB |

A "meme × stock" pool is one whose non-stock leg is **not** a quote asset. Reuse
`chain.is_meme_pair()` and `chain.MAJORS` — do not re-derive the list. Pools against USDG,
WETH or native ETH are the stock's own market, not a meme launch.

**Done when:** all four files exist, each under budget, and `data/memes.json` contains the
GUH/AAPL pool `0xaf430d97f3dc5f14aa0042e3f398ba288e51707eac001bf8b46dea1327a6e1cb`.

## Task 2 — Static site shell

`site/index.html`, `site/app.js`, `site/styles.css`. Three tabs: **Memes × Stocks** (default),
**Stocks**, **All stock-paired pools**, plus a search box. Loads `data/*.json` by `fetch`
(this is a hosted page, so fetch is correct here).

- Sortable columns; Memes defaults to swaps desc, Stocks to launch date asc (oldest first).
- Theme-aware: full light palette on bare `:root`, dark overrides under both
  `@media (prefers-color-scheme: dark)` guarded with `:root:not([data-theme="light"])` and
  `:root[data-theme="dark"]`. Give `body` an explicit background.
- Wide tables scroll inside their own `overflow-x:auto` container; the page body must never
  scroll horizontally.
- Escape every token-derived string before it reaches `innerHTML` — token names are
  attacker-controlled and one is literally named `</script><b>pwn`.

**Done when:** renders from committed `data/` with zero console errors, on desktop and mobile.

## Task 3 — Live price loop (this is what makes it "live")

Poll GeckoTerminal from the browser for **only the rows currently on screen**.

- After sort/filter, take the visible ~60 rows, batch into
  `GET /api/v2/networks/robinhood/pools/multi/{up to 30 ids}` → 2 calls, every 20–30s.
- **Orientation:** `price_change_percentage` describes the pool's BASE token. When the stock is
  the base, invert for the meme leg with `1/(1+r)-1` — **not** negation. Getting this wrong
  silently reports the sign backwards.
- **Never use a pool's `market_cap_usd`** (it is the base token's — the AAPL/USDG pool reports
  $3.33e9, which is USDG).
- **Discard implausible reserves**: `> $500M`, or `> 100 × fdv`. A real pool reported
  `reserve_in_usd = 2.6e49`.
- Flash cells green/red on change; show "updated Ns ago"; a live/stale indicator that goes
  amber on 429 or fetch failure and keeps showing last-known values **with their age** — never
  blanks, never zeros.
- Back off to 60s on 429. Budget: ~6 of GeckoTerminal's ~30 calls/min per viewer.

**Done when:** prices visibly change without reload; devtools shows `pools/multi` firing on the
interval and only for visible rows; throttling the network turns the indicator amber.

## Task 4 — Live new-launch feed

Poll `GET /api/v2/networks/robinhood/new_pools` every 30s. Keep only pools whose quote is one of
the 203 stock addresses from `data/stocks.json`, and inject them as **NEW** rows with a ticking
age. This is the meta arriving in real time and is the most valuable thing on the page.

**Done when:** a stock-quoted pool created in the last minute appears within one poll cycle,
cross-checked against `curl .../new_pools`.

## Task 5 — Ticker search

Lazy-load `data/tickers.json` on first keystroke (do not block initial paint on 1.5 MB). Group
hits by ticker, **oldest first**, badge each **REGISTRY** or **UNVERIFIED**, and visually group
rows sharing a deployer as a farm.

Registry membership is the verdict; age only corroborates it. Lead with the badge.

**Done when:** `GME` returns ~270 rows with exactly one badged REGISTRY.

## Task 6 — Scheduled refresh workflow

`.github/workflows/refresh.yml`: `schedule` every 30 min + `workflow_dispatch`.

```
restore actions/cache (data/)  →  pipeline/pools.py   (incremental)
                               →  pipeline/live.py --window 6h
                               →  pipeline/symbols.py
                               →  build/export.py
                               →  deploy site/ + data/ to Pages
                               →  save cache
```

- **`collect.py` is deliberately NOT in the cron path.** Pricing happens in the viewer's
  browser; CI only needs the RPC. This is what keeps CI fast and avoids GeckoTerminal
  throttling a shared runner IP.
- Never commit raw state — `.gitignore` already blocks it. Deploy with `force_orphan: true` (or
  an equivalent squash) so 30-minute refreshes never accumulate history.
- `concurrency` with `cancel-in-progress: false`, so a slow sweep is never cut mid-run.
- A cache miss is survivable: `pools.py --full` cold-rebuilds in ~90s of sweeping.

Enable Pages on first deploy (Settings → Pages → source: GitHub Actions). The published site
is **https://litre0.github.io/rchain-stocks**.

**Done when:** two consecutive scheduled runs succeed, the second is incremental,
`git count-objects -vH` shows no 20 MB blob in history, and the Pages URL serves the site.

---

## The one rule

**Never reintroduce vendor per-token listings for discovery.** They are capped and not sorted
by liquidity, so they silently drop live pools — including the ones this project exists to
surface. Pools come from the chain sweep; vendors price them by pool id.

`pipeline/verify.py` asserts GUH/AAPL, CLARUS/AAPL and PINE/AAPL are present. All three are
absent from DexScreener's AAPL listing. If that test fails, discovery has regressed.

## Verification fixtures

- 203 stock tokens; oldest `WEEK` `2026-05-27T20:17:41Z`, newest `BND` `2026-07-28T15:11:34Z`
- AAPL appears in **1,564** pools (vendors see 36)
- `GME` ticker claimed by ~270 tokens, exactly one in the registry
- `fixtures/stocks.json` is the committed 203-token registry for offline testing

**Offline, `pipeline/verify.py` runs 7 registry assertions and then reports
`(pools.json missing)` and exits 0.** That is expected: the other 20 checks — pool counts, the
coverage regression, ticker collisions — need `pools.json`, which is 20 MB and deliberately not
committed. To run the full 27, do a local pipeline run first (`pools.py`, `symbols.py`,
`live.py`, `collect.py`), or let CI run it with the cache warm.
