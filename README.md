# rchain-stocks

**A local terminal for tokenized stocks and the memecoins launching against them on
Robinhood Chain (chain ID 4663).**

Every tokenized stock on the chain, every pool that quotes a memecoin against one, and which
of those are actually trading — enumerated from the chain itself rather than from a vendor's
listing endpoint.

**This runs on your machine. There is no hosted site and nothing phones home.**
`dashboard.html` is committed with a snapshot already inlined, so a fresh clone opens with
data in it. When you want fresher numbers you re-run the pipeline yourself — see
[Refreshing](#refreshing) and `CLAUDE.md`.

> ⚠️ **Not financial advice. Not a signal service.** This is a data and verification tool.
> Most memecoins on any chain go to zero. Do your own research, and never risk money you
> cannot afford to lose.

---

## Open it

```bash
git clone https://github.com/Litre0/rchain-stocks.git
cd rchain-stocks
```

Then open `dashboard.html` in a browser. Any of these work:

```bash
xdg-open dashboard.html     # Linux
open dashboard.html         # macOS
start dashboard.html        # Windows
```

Or paste the file path straight into the address bar — `file:///…/rchain-stocks/dashboard.html`.

**No server, no build step, no install.** The snapshot is inlined into the HTML as a
`<script type="application/json">` block rather than fetched, precisely so `file://` works —
a `file://` page cannot `fetch()` a sibling JSON under CORS. The only outbound requests the
page makes are Google Fonts (it renders fine offline without them) and the DexScreener links
you click.

### What you are looking at

Four tabs — **Memes × Stocks**, **Stocks**, **All stock-paired pools**, and a search box —
sortable on every column, with 5m/15m/30m/1h/4h/6h/24h change buckets and a light/dark theme
that follows your OS.

The committed snapshot was generated `2026-08-30T11:45:45Z` over a 6h liveness window:

| | |
|---|---|
| Tokenized stock tokens (exhaustive, from the issuer factory) | **203** |
| Pools with a stock token as a leg | **35,357** |
| Of those, memecoin × stock pools | **30,995** |
| Trading in the 6h window | **1,291** (855 of them meme × stock) |
| Distinct tokens in the search index | **27,311** |
| Tokens impersonating a real stock ticker | **1,649** |

## Refreshing

The dashboard is a snapshot, not a live feed. To regenerate it, run the pipeline — Python 3
standard library only, **no API keys, no accounts, no install**:

```bash
python3 pipeline/registry.py           # 203 stock tokens from the factory   (seconds)
python3 pipeline/pools.py              # every stock-paired pool ever created (slow once, then incremental)
python3 pipeline/symbols.py            # ticker/name per counterparty + impostor dating
python3 pipeline/live.py --window 6h   # which pools are actually trading
python3 pipeline/collect.py            # price the live set via GeckoTerminal
python3 pipeline/render.py             # -> dashboard.html
```

Then reload the page.

**The first run is the slow one.** `pools.py` sweeps the chain from genesis and `symbols.py`
reads a ticker for every counterparty; budget several minutes each. Both checkpoint to
`data/`, so every later run resumes from a cursor and takes seconds. Raw state is gitignored
(`pools.json` alone is 20 MB and goes stale within minutes), which is why the clone ships the
rendered HTML instead of the JSON behind it.

Day to day you only need the last three:

```bash
python3 pipeline/live.py --window 6h && python3 pipeline/collect.py && python3 pipeline/render.py
```

`collect.py` is the wall-clock cost — GeckoTerminal's free tier is ~30 calls/min and the
collector paces itself to ~23/min to stay under it. `python3 pipeline/verify.py` runs the
assertion battery when you want to check nothing has regressed.

### Refreshing with Claude or another LLM

`CLAUDE.md` in this repo is a runbook written for a coding agent: the refresh order, how long
each stage should take, what the failure modes look like, and the invariants that must not be
broken. Point Claude Code (or any agent that reads repo instructions) at this directory and
ask it to refresh the dashboard — it will read that file and drive the pipeline for you.

---

## Why this exists: vendor listings are not complete

The obvious way to build this is to ask DexScreener or GeckoTerminal for each stock token's
pairs. That was measured, and it does not work. Their **per-token** pair listings are hard
capped and — critically — **not sorted by liquidity**, so they silently drop live pools.

Against the AAPL pools that exist on chain:

| Source | AAPL pools returned |
|---|---|
| DexScreener `/token-pairs/v1/robinhood/{token}` | 30 (hard cap) |
| GeckoTerminal `/tokens/{token}/pools` | 20 (hard cap) |
| Union of both | **36** |

DexScreener's 30 include pools with **$16** and **$0** liquidity while omitting
CLARUS/AAPL ($118k), GUH/AAPL ($92k) and PINE/AAPL ($35k).

So pools are enumerated from pool-creation events chain-wide, and vendors are used only as a
**pricing service addressed by pool id** — per-pool lookups are uncapped. `pipeline/verify.py`
carries a regression test asserting those three pools are present; if anyone reintroduces
listing-based discovery, it fails.

## The stock-token registry

Every stock token is an OpenZeppelin BeaconProxy minted by one factory,
`0x4783C67b63dE2B358Ac5951a7D41F47A38F3C046`, which emits `(address token, string name,
string symbol)` per deployment (topic0 `0xd9b0c6a1…76d6`). That event is the **only** complete
enumeration — OZ 5.x dropped `BeaconUpgraded`, so there are no proxy-side logs to scan.

The factory is **chain-wide, not launchpad-scoped**: launchpads (Pons, Hoodit, Clanker, Bankr)
mint the memecoins and merely *select* a stock token as a pool's quote asset.

Oldest stock token: `WEEK` (Roundhill Weekly T-Bill ETF). Newest: `BND` (Vanguard Total Bond
Market ETF).

## Ticker collisions — what the search box is for

Symbols are not unique here. **270** tokens call themselves `GME`, **70** call themselves
`AAPL`, and 310 claim `NETFLIX` — with deliberately confusable names like
`Apple Inc. Common Stock`. There are no duplicate symbols inside the 203, so exactly one of
each is real.

Search groups every token sharing a ticker, oldest first, and badges each **REGISTRY** or
**UNVERIFIED**. **Registry membership is the proof; age only corroborates it** — every
impostor found so far deployed later than the real token, but an impostor could front-run a
future listing, so the badge leads. Deployers that minted more than one colliding token are
flagged as a farm.

## Data sources

| Source | Cost | Auth | Role |
|---|---|---|---|
| `rpc.mainnet.chain.robinhood.com` | free | none | everything authoritative |
| GeckoTerminal API v2 | free | none, ~30 req/min | per-pool pricing, OHLCV |
| DexScreener API | free | none | cross-check |

Override the RPC with `RH_RPC=<url>` and the state directory with `RH_DATA=<path>`.

**Blockscout is deliberately not used.** It is free and keyless but sits behind Cloudflare — a
bare `curl` gets an interstitial and only returns JSON if you spoof a browser `User-Agent`.
That is scraping. Everything it offered has a native RPC equivalent (contract dates come from
first `Transfer` logs instead).

## Timeframes

| Bucket | Source |
|---|---|
| 5m / 30m / 6h / 24h | GeckoTerminal `price_change_percentage` |
| 15m / 1h / **4h** | derived from one 15-minute OHLCV series per pool |

No API publishes a 4h. One call to
`/pools/{pool}/ohlcv/minute?aggregate=15&limit=18&currency=usd&token={addr}` returns 4.5h of
candles, and 15m/1h/4h all come off that one self-consistent series. The `token=` parameter
makes GeckoTerminal return the leg we care about already the right way up.

## Known limits — read before trusting output

- **Pool orientation flips.** `price_change_percentage` describes the pool's BASE token, and
  many pools have the stock as base. Inverting is `1/(1+r)-1`, not negation.
- **A pool's `market_cap_usd` is the base token's cap.** The AAPL/USDG pool reports $3.33e9 —
  that is USDG. Caps come from the token endpoint or `totalSupply × price`.
- **Corrupt vendor reserves exist.** GeckoTerminal reported `reserve_in_usd = 2.6e49` for a
  pool with $25k FDV. Implausible reserves are discarded, not displayed.
- **Pool dates older than ~24h are interpolated** (`stride=8`) and flagged `created_approx`.
  Measured error: 0.0–0.5s in the modern chain, at most ~67s in the earliest blocks. Anything
  within ~24h of head is exact. `pools.py --exact-dates` disables it and takes the hour.
- **The public RPC is not an archive node** — `eth_getCode` at a historical block fails, so
  contracts are dated by first `Transfer` log.
- **Block rate is non-uniform** (~4/s early, ~10/s now). Never convert blocks↔time with a
  constant; `chain.block_at_time()` binary-searches real timestamps.
- **`eth_getLogs` caps at 10,000 matched logs** and times out on wide ranges; sustained
  querying returns 429. `chain.sweep()` bisects on cap/timeout and backs off on 429 —
  bisecting a 429 makes it worse.
- **RPC batches over 25 sub-requests are rejected wholesale with a 429.** `chain.batch()`
  chunks to 25.
- **V4 reserves are unreadable** (singleton PoolManager); liquidity comes from GeckoTerminal
  where indexed and is blank otherwise — **not zero**.
- **A bucket with no data renders `—`, never 0.** A pool younger than 4h has no 4h number.
- Market caps move with mint/burn issuance — not a fixed float. A climbing cap means net
  minting.
- `pools.py` reports its own unscanned ranges. If it warns, the count is a lower bound:
  re-run with `--retry-gaps`.

## Licence

MIT. See `LICENSE`.
