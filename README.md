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

The dashboard is a snapshot, not a live feed. To regenerate it from the chain — Python 3
standard library only, **no API keys, no accounts, no install**:

```bash
./refresh.sh
```

Then reload the page. That is the whole thing. It works out whether this is a fresh clone or
an update, runs the pipeline stages in the right order, verifies the result, and prints the
`file://` path to reopen.

```bash
./refresh.sh --quick        # prices only; skips pool/ticker discovery (fastest)
./refresh.sh --full         # re-sweep pool discovery from genesis
./refresh.sh --window 24h   # widen the liveness window (default 6h)
```

Use the default when you want **new launches** to show up; `--quick` when you only want
current prices on pools already known.

<details>
<summary>Running the stages by hand</summary>

The order is fixed — each stage reads what the previous one wrote. Never run them in
parallel; they chain through files and share one rate-limit budget.

```bash
python3 pipeline/registry.py           # 203 stock tokens from the factory   (~20 s)
python3 pipeline/pools.py              # every stock-paired pool ever created (slow once, then incremental)
python3 pipeline/symbols.py            # ticker/name per counterparty + impostor dating
python3 pipeline/live.py --window 6h   # which pools are actually trading
python3 pipeline/collect.py            # price the live set via GeckoTerminal
python3 pipeline/render.py             # -> dashboard.html
python3 pipeline/verify.py             # assertions
```

</details>

### How long a refresh takes

All measured on a real run, not estimated. **A refresh is minutes-to-an-hour, not seconds** —
budget for it.

The first run pays two one-off costs: `pools.py` 27 min sweeping pool-creation events from
genesis, and `symbols.py` 17 min reading a ticker for every counterparty. Both checkpoint to
`data/`, so later runs skip them.

What does *not* get cheaper is the liveness sweep. `live.py` keeps no cursor and re-sweeps its
entire window every run. That window is the dominant cost, and it scales linearly:

| `./refresh.sh --window` | blocks swept | `live.py` takes |
|---|---|---|
| `30m` | ~18k | ~4 min |
| `1h` | ~36k | ~8 min |
| `6h` *(default)* | 213k | **~46 min** (measured) |
| `24h` | ~854k | **~3 hours** |

`collect.py` then prices whatever the sweep found, which scales with it too — **40 min** at
the 6h window.

A full cold build was measured end to end at **2h 10m** (registry 21s, pools 27 min, symbols
17 min, live 46 min, collect 40 min, render <1s) and produced a valid dashboard with 27/27
checks passing.

So for a routine refresh use **`./refresh.sh --window 1h`**, which shrinks both the sweep and
the pricing that follows it. Only the 6h path above is measured; budget roughly 20–30 minutes
for a 1h window. The 6h default matches the shipped snapshot and shows more pools, but costs
the better part of an afternoon on a cold clone.

`--quick` skips pool and ticker discovery, but still runs the sweep — it does not rescue a 6h
window. None of these stages is hung when quiet; they print little while sweeping.

Raw state is gitignored (`pools.json` alone is 20 MB and goes stale within minutes), which is
why the clone ships the rendered HTML instead of the JSON behind it.

`collect.py` is the wall-clock cost — GeckoTerminal's free tier is ~30 calls/min and the
collector paces itself to ~23/min to stay under it, so a refresh is minutes rather than
seconds. That pacing is deliberate; raising it trips a 429 and the run produces nothing.

### Refreshing with Claude or another LLM

The repo ships a **Claude Code skill**. Clone it, open Claude Code in the directory, and just
ask — "refresh the dashboard", "what's trading against TSLA", "is this GME the real one":

```
$ claude
> refresh the dashboard
```

The skill (`.claude/skills/refresh-dashboard/`) is picked up automatically from the repo. It
tells the agent how stale the snapshot is, which refresh path to take, how long each stage
should take so it doesn't mistake slow for hung, what the failure modes mean, and — usefully —
how to answer questions straight out of `data/snapshot.json` instead of making you read a
table. It also carries the invariants that must not be broken.

`CLAUDE.md` and `AGENTS.md` cover the same ground as plain context files, for agents that read
those instead of skills.

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
