# rchain-stocks

**A live terminal for tokenized stocks and the memecoins launching against them on
Robinhood Chain (chain ID 4663).**

Every tokenized stock on the chain, every pool that quotes a memecoin against one, and which
of those are actually trading right now — enumerated from the chain itself rather than from a
vendor's listing endpoint.

> ⚠️ **Not financial advice. Not a signal service.** This is a data and verification tool.
> Most memecoins on any chain go to zero. Do your own research, and never risk money you
> cannot afford to lose.

---

## Why this exists: vendor listings are not complete

The obvious way to build this is to ask DexScreener or GeckoTerminal for each stock token's
pairs. That was measured, and it does not work. Their **per-token** pair listings are hard
capped and — critically — **not sorted by liquidity**, so they silently drop live pools.

Against **1,564** AAPL pools that exist on chain:

| Source | AAPL pools returned |
|---|---|
| DexScreener `/token-pairs/v1/robinhood/{token}` | 30 (hard cap) |
| GeckoTerminal `/tokens/{token}/pools` | 20 (hard cap) |
| Union of both | **36 of 1,564** |

DexScreener's 30 include pools with **$16** and **$0** liquidity while omitting
CLARUS/AAPL ($118k), GUH/AAPL ($92k) and PINE/AAPL ($35k).

So pools are enumerated from pool-creation events chain-wide, and vendors are used only as a
**pricing service addressed by pool id** — per-pool lookups are uncapped. `pipeline/verify.py`
carries a regression test asserting those three pools are present; if anyone reintroduces
listing-based discovery, it fails.

## What it finds

| | |
|---|---|
| Tokenized stock tokens (exhaustive, from the issuer factory) | **203** |
| Pools with a stock token as a leg | **35,357** |
| Of those, memecoin × stock pools | **30,995** |
| Distinct tokens ever paired with a stock | **28,243** |
| Tokens impersonating a real stock ticker | **1,649** |

Ticker collisions are rampant — **270** tokens call themselves `GME`, 70 call themselves
`AAPL` — with deliberately confusable names like `Apple Inc. Common Stock`. There are no
duplicate symbols inside the 203, so exactly one of each is real. Search badges every token
**REGISTRY** or **UNVERIFIED**, oldest first, and groups deployers that minted several.

## The stock-token registry

Every stock token is an OpenZeppelin BeaconProxy minted by one factory,
`0x4783C67b63dE2B358Ac5951a7D41F47A38F3C046`, which emits `(address token, string name,
string symbol)` per deployment (topic0 `0xd9b0c6a1…76d6`). That event is the **only** complete
enumeration — OZ 5.x dropped `BeaconUpgraded`, so there are no proxy-side logs to scan.

The factory is **chain-wide, not launchpad-scoped**: launchpads (Pons, Hoodit, Clanker, Bankr)
mint the memecoins and merely *select* a stock token as a pool's quote asset.

Oldest stock token: `WEEK` (Roundhill Weekly T-Bill ETF) at `2026-05-27T20:17:41Z`.
Newest: `BND` at `2026-07-28T15:11:34Z`.

## Architecture

The Robinhood RPC sends **no CORS headers**, so the chain sweep cannot run in a browser.
GeckoTerminal sends `access-control-allow-origin: *`, so pricing can. The split follows rate
of change, not convenience:

- **CI (scheduled):** the 203-stock registry, the pool map, which pools are trading (RPC swap
  sweep), the ticker index.
- **Viewer's browser:** price, liquidity, volume and the 5m/15m/1h/24h buckets for the rows on
  screen, plus a live new-launch feed — each visitor spends their own rate-limit budget.

## Running the pipeline yourself

Python 3 standard library only. No API keys, no accounts, no scraping.

```bash
python3 pipeline/registry.py          # 203 stock tokens (seconds)
python3 pipeline/pools.py             # every stock-paired pool; slow once, then incremental
python3 pipeline/symbols.py           # tickers + impostor dating
python3 pipeline/live.py --window 6h  # which pools are trading
python3 pipeline/collect.py           # price via GeckoTerminal
python3 pipeline/verify.py            # assertions
```

## Data sources

| Source | Cost | Auth | Role |
|---|---|---|---|
| `rpc.mainnet.chain.robinhood.com` | free | none | everything authoritative |
| GeckoTerminal API v2 | free | none, ~30 req/min | per-pool pricing, OHLCV |
| DexScreener API | free | none | cross-check |

**Blockscout is deliberately not used.** It is free and keyless but sits behind Cloudflare — a
bare `curl` gets an interstitial and only returns JSON if you spoof a browser `User-Agent`.
That is scraping. Everything it offered has a native RPC equivalent (contract dates come from
first `Transfer` logs instead).

## Known limits — read before trusting output

- **Pool orientation flips.** `price_change_percentage` describes the pool's BASE token, and
  many pools have the stock as base. Inverting is `1/(1+r)-1`, not negation.
- **A pool's `market_cap_usd` is the base token's cap.** The AAPL/USDG pool reports $3.33e9 —
  that is USDG. Caps come from the token endpoint or `totalSupply × price`.
- **Corrupt vendor reserves exist.** GeckoTerminal reported `reserve_in_usd = 2.6e49` for a
  pool with $25k FDV. Implausible reserves are discarded, not displayed.
- **Pool dates older than ~24h are interpolated** (`stride=8`) and flagged `created_approx`.
  Measured error: 0.0–0.5s in the modern chain, at most ~67s in the earliest blocks. Anything
  within ~24h of head is exact.
- **The public RPC is not an archive node** — `eth_getCode` at a historical block fails, so
  contracts are dated by first `Transfer` log.
- **Block rate is non-uniform** (~4/s early, ~10/s now). Never convert blocks↔time with a
  constant.
- **V4 reserves are unreadable** (singleton PoolManager); liquidity comes from GeckoTerminal
  where indexed and is blank otherwise — **not zero**.
- **A bucket with no data renders `—`, never 0.** A pool younger than 4h has no 4h number.
- Market caps move with mint/burn issuance — not a fixed float.

## Licence

MIT. See `LICENSE`.
