#!/usr/bin/env python3
"""Price the chain-discovered universe and emit the dashboard snapshot.

Vendors are used as a PRICING service addressed by pool, never as a discovery
service: per-pool lookups are uncapped, per-token pair listings are not.

Two orientation traps, both handled here:
  * GeckoTerminal's price_change_percentage describes the pool's BASE token.
    Half these pools have the stock as base, so the series must be inverted
    when we want the other leg (a +p move in base is 1/(1+p)-1 in quote).
  * A pool's market_cap_usd is the BASE token's cap -- the AAPL/USDG pool
    reports $3.33e9, which is USDG. Caps come from the token endpoint only.

The 15m/1h/4h buckets are derived from one 15-minute OHLCV series per pool,
requested with token=<address> so GeckoTerminal returns the leg we care about
already the right way up. No API publishes a 4h.

    python3 pipeline/collect.py
    python3 pipeline/collect.py --max-ohlcv 200
"""
import json, os, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chain as C

GT = "https://api.geckoterminal.com/api/v2/networks/robinhood"
# GeckoTerminal's free tier is ~30 calls/min. 2.2s (27/min) sat right on the
# edge and tripped a 429 mid-run; at 2.6s (~23/min) a full run completes. The
# old code fetched without checking HTTP status, so it could not SEE the 429 --
# it just retried blind, burning 25 minutes to produce nothing.
THROTTLE = float(os.environ.get("RH_GT_THROTTLE", "2.6"))
_last = [0.0]
_ohlcv_cache = {}


def gt(path):
    for attempt in range(5):
        wait = THROTTLE - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        out = subprocess.run(
            ["curl", "-s", "-m", "30", "-w", "\n%{http_code}", f"{GT}/{path}"],
            capture_output=True, text=True).stdout
        _last[0] = time.time()
        body, _, code = out.rpartition("\n")
        if code.strip() == "429":
            back = 20 * (attempt + 1)
            C.log(f"  GeckoTerminal 429 -- backing off {back}s")
            time.sleep(back)
            continue
        try:
            d = json.loads(body)
        except Exception:
            time.sleep(3)
            continue
        if isinstance(d, dict) and "data" in d:
            return d
        time.sleep(3)
    return None


def f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


MAX_PLAUSIBLE_LIQ = 5e8          # no pool on this chain is near $500M


def sane_liq(liq, fdv):
    """Drop corrupt vendor reserves.

    GeckoTerminal reported reserve_in_usd = 2.6e49 for POTATO/AAPL, a pool
    whose FDV is $25k and 24h volume $14.8k. A bogus reserve passes every
    liquidity gate and implies an unlimited position size, so it must be
    rejected rather than displayed.
    """
    if liq is None:
        return None
    if liq > MAX_PLAUSIBLE_LIQ:
        return None
    if fdv and liq > fdv * 100:
        return None
    return liq


def invert_pct(p):
    """A +p% move in the base leg is this much in the quote leg."""
    if p is None:
        return None
    r = p / 100.0
    if r <= -1:
        return None
    return (1.0 / (1.0 + r) - 1.0) * 100.0


def changes_for(attrs, want_addr):
    """Vendor buckets oriented to want_addr, whichever leg it is."""
    base = (attrs.get("_base") or "").lower()
    pc = attrs.get("price_change_percentage") or {}
    out = {k: f(pc.get(k)) for k in ("m5", "m15", "m30", "h1", "h6", "h24")}
    if base and want_addr and base != want_addr:
        out = {k: invert_pct(v) for k, v in out.items()}
    return out


def ohlcv_buckets(pool, token):
    """15m / 1h / 4h from one self-consistent 15-minute series."""
    key = f"{pool}:{token}"
    if key in _ohlcv_cache:
        return _ohlcv_cache[key]
    d = gt(f"pools/{pool}/ohlcv/minute?aggregate=15&limit=18&currency=usd&token={token}")
    if not d:
        return {}, None
    lst = (d.get("data", {}).get("attributes", {}) or {}).get("ohlcv_list") or []
    if not lst:
        _ohlcv_cache[key] = ({}, None)
        return {}, None
    lst = sorted(lst, key=lambda c: -c[0])        # newest first
    now = lst[0][4]
    out = {}
    for label, back in (("m15", 1), ("h1", 4), ("h4", 16)):
        if len(lst) > back and lst[back][1]:
            out[label] = (now / lst[back][1] - 1.0) * 100.0
        else:
            out[label] = None                      # too young -- never 0
    _ohlcv_cache[key] = (out, now)
    return out, now


def multi_pools(pool_ids):
    """GeckoTerminal pools/multi, 30 at a time. Returns {pool_id: attrs}."""
    got = {}
    for i in range(0, len(pool_ids), 30):
        part = pool_ids[i:i + 30]
        d = gt("pools/multi/" + ",".join(part))
        if not d:
            continue
        for p in d.get("data", []):
            a = dict(p["attributes"])
            rel = p.get("relationships") or {}
            b = ((rel.get("base_token") or {}).get("data") or {}).get("id", "")
            q = ((rel.get("quote_token") or {}).get("data") or {}).get("id", "")
            a["_base"] = b.split("_")[-1].lower() if b else ""
            a["_quote"] = q.split("_")[-1].lower() if q else ""
            a["_dex"] = (((rel.get("dex") or {}).get("data") or {}).get("id") or "")
            got[a["address"].lower()] = a
        C.log(f"  priced {min(i+30, len(pool_ids))}/{len(pool_ids)} pools")
    return got


def deepest_pool(stock_addr):
    """Pick one pool to price a stock from.

    This calls the per-token pool listing -- the capped endpoint we refuse to
    use for DISCOVERY -- but here we only need a single good pool to read a
    price series off, and the deepest one is always in the listing. Coverage
    of the pool universe still comes from the chain sweep.
    """
    d = gt(f"tokens/{stock_addr}/pools")
    if not d:
        return None, None
    best, liq = None, -1.0
    for p in d.get("data", []):
        a = p["attributes"]
        r = f(a.get("reserve_in_usd")) or 0.0
        if r > liq:
            rel = p.get("relationships") or {}
            b = ((rel.get("base_token") or {}).get("data") or {}).get("id", "")
            a = dict(a)
            a["_base"] = b.split("_")[-1].lower() if b else ""
            best, liq = a, r
    return (best["address"].lower(), best) if best else (None, None)


def main():
    args = sys.argv[1:]
    max_ohlcv = int(args[args.index("--max-ohlcv") + 1]) if "--max-ohlcv" in args else 120
    max_stocks = int(args[args.index("--max-stocks") + 1]) if "--max-stocks" in args else 50

    reg, pl = C.load("stocks.json"), C.load("pools.json")
    live, toks = C.load("live.json"), C.load("tokens.json")
    if not (reg and pl):
        sys.exit("run stocks/registry.py and stocks/pools.py first")
    pools, tokens = pl["pools"], (toks or {}).get("tokens", {})

    # ---------- tab 1: the 203 stock tokens ----------
    stock_rows = {t["address"]: dict(t, pools=0, best_pool=None, best_liq=0.0)
                  for t in reg["tokens"]}
    for k, p in pools.items():
        if p["stock"] in stock_rows:
            stock_rows[p["stock"]]["pools"] += 1

    C.log(f"pricing {len(stock_rows)} stock tokens")
    addrs = list(stock_rows)
    for i in range(0, len(addrs), 30):
        part = addrs[i:i + 30]
        d = gt("tokens/multi/" + ",".join(part))
        if not d:
            continue
        for t in d.get("data", []):
            a = t["attributes"]
            r = stock_rows.get(a["address"].lower())
            if not r:
                continue
            r["price_usd"] = f(a.get("price_usd"))
            r["market_cap_usd"] = f(a.get("market_cap_usd")) or f(a.get("fdv_usd"))
            r["fdv_usd"] = f(a.get("fdv_usd"))
            r["volume_24h"] = f((a.get("volume_usd") or {}).get("h24"))
            r["reserve_usd"] = f(a.get("total_reserve_in_usd"))

    # ---------- tab 2: stock-paired pools ----------
    live_rows = (live or {}).get("pools", [])
    if not live_rows:
        C.log("no live.json -- falling back to newest pools by creation")
        live_rows = [{"pool": k, "swaps": None, **v} for k, v in
                     sorted(pools.items(), key=lambda kv: -(kv[1].get("created_ts") or 0))[:300]]

    # Budget is limited, so spend it on memes first. Ranked purely by swaps the
    # list fills with AAPL/USDG-style pools -- the stocks' own markets -- and
    # the actual meme meta never gets priced.
    meme_live = [r for r in live_rows if C.is_meme_pair(pools.get(r["pool"], {}))]
    rest_live = [r for r in live_rows if not C.is_meme_pair(pools.get(r["pool"], {}))]
    budget = max(max_ohlcv * 3, 300)
    ranked = meme_live[:int(budget * 0.8)] + rest_live[:budget - int(budget * 0.8)]
    C.log(f"pricing {len(ranked)} pools ({len(meme_live):,} live meme pairs available)")
    priced = multi_pools([r["pool"] for r in ranked])

    pair_rows = []
    for r in ranked:
        a = priced.get(r["pool"])
        p = pools.get(r["pool"], {})
        other = p.get("other")
        meta = tokens.get(other, {})
        row = {
            "pool": r["pool"], "kind": p.get("kind"), "dex": (a or {}).get("_dex"),
            "stock": p.get("stock"), "stock_symbol": p.get("stock_symbol"),
            "other": other,
            "symbol": meta.get("symbol") or "?",
            "name": meta.get("name") or "",
            "registry_other": meta.get("registry", False),
            "created": p.get("created"), "created_ts": p.get("created_ts"),
            "swaps": r.get("swaps"),
            "meme": C.is_meme_pair(p),
            "indexed": bool(a),
        }
        if a:
            row["gt_name"] = a.get("name")
            row["liquidity_usd"] = sane_liq(f(a.get("reserve_in_usd")),
                                            f(a.get("fdv_usd")))
            row["liq_rejected"] = (f(a.get("reserve_in_usd")) is not None
                                   and row["liquidity_usd"] is None)
            row["volume_24h"] = f((a.get("volume_usd") or {}).get("h24"))
            row["volume_1h"] = f((a.get("volume_usd") or {}).get("h1"))
            base = a.get("_base")
            row["price_usd"] = (f(a.get("base_token_price_usd")) if base == other
                                else f(a.get("quote_token_price_usd")))
            row["fdv_usd"] = f(a.get("fdv_usd")) if base == other else None
            row["chg"] = changes_for(a, other)
            tx = a.get("transactions") or {}
            row["txns_24h"] = (tx.get("h24") or {}).get("buys"), (tx.get("h24") or {}).get("sells")
        else:
            row["chg"] = {}
        pair_rows.append(row)

    # memes first for the derived-bucket budget, then by depth
    pair_rows.sort(key=lambda r: (not r.get("meme"), -(r.get("liquidity_usd") or 0)))
    for r in pair_rows[:max_ohlcv]:
        if not r["indexed"]:
            continue
        b, _ = ohlcv_buckets(r["pool"], r["other"])
        r["chg"].update({k: v for k, v in b.items() if v is not None or k not in r["chg"]})
        r["chg_derived"] = True

    # ---------- stock buckets, oriented to the STOCK leg ----------
    # A stock's deepest pool is often one where the stock is the QUOTE (a
    # memecoin quoted in AAPL). Reading that pool's buckets raw would report
    # the memecoin's move as the stock's, so vendor buckets go through
    # changes_for(..., stock_address) and the OHLCV series is requested with
    # token=<stock> so GeckoTerminal returns it already the right way up.
    best = {}
    for r in pair_rows:
        s_addr = r.get("stock")
        if not s_addr or not r["indexed"]:
            continue
        if (r.get("liquidity_usd") or 0) > (best.get(s_addr, {}).get("liquidity_usd") or 0):
            best[s_addr] = r
    for s_addr, r in stock_rows.items():
        bp = best.get(s_addr)
        if not bp:
            continue
        r["best_pool"] = bp["pool"]
        r["best_liq"] = bp.get("liquidity_usd")
        a = priced.get(bp["pool"])
        if a:
            r["chg"] = changes_for(a, s_addr)

    top_stocks = sorted(stock_rows.values(), key=lambda r: -(r.get("volume_24h") or 0))[:max_stocks]
    C.log(f"deriving buckets for the top {len(top_stocks)} stocks by volume")
    for r in top_stocks:
        if not r.get("best_pool"):
            pool, attrs = deepest_pool(r["address"])
            if not pool:
                continue
            r["best_pool"] = pool
            r["best_liq"] = f(attrs.get("reserve_in_usd"))
            r["chg"] = changes_for(attrs, r["address"])
        b, _ = ohlcv_buckets(r["best_pool"], r["address"])
        r.setdefault("chg", {})
        r["chg"].update({k: v for k, v in b.items() if v is not None or k not in r["chg"]})

    quote_meta = {}
    for r in live_rows:
        p = pools.get(r["pool"], {})
        if not C.is_meme_pair(p):
            continue
        q = quote_meta.setdefault(p.get("stock_symbol") or "?",
                                  {"symbol": p.get("stock_symbol"), "pools": 0,
                                   "swaps": 0, "newest": None})
        q["pools"] += 1
        q["swaps"] += r.get("swaps") or 0
        if p.get("created_ts") and (q["newest"] or 0) < p["created_ts"]:
            q["newest"] = p["created_ts"]
    quote_meta = sorted(quote_meta.values(), key=lambda q: -q["swaps"])

    all_meme_pools = sum(1 for p in pools.values() if C.is_meme_pair(p))
    snap = {
        "generated": C.iso(int(time.time())),
        "quote_meta": quote_meta,
        "window": (live or {}).get("window"),
        "provenance": {
            "stocks_total": len(stock_rows),
            "pools_total": pl["count"],
            "pools_gaps": len(pl.get("gaps") or []),
            "live_total": len(live_rows),
            "pools_priced": sum(1 for r in pair_rows if r["indexed"]),
            "pools_unindexed": sum(1 for r in pair_rows if not r["indexed"]),
            "impostors": len((toks or {}).get("collisions") or []),
            "meme_pools_total": all_meme_pools,
            "meme_pools_live": len(meme_live),
        },
        "stocks": sorted(stock_rows.values(), key=lambda r: r["deployed_ts"] or 0),
        "pairs": pair_rows,
        "tokens": tokens,
    }
    C.save("snapshot.json", snap)
    p = snap["provenance"]
    print(f"  meme x stock: {p['meme_pools_live']:,} live of {p['meme_pools_total']:,} ever")
    print(f"snapshot: {p['stocks_total']} stocks, {len(pair_rows)} pairs "
          f"({p['pools_priced']} priced, {p['pools_unindexed']} not indexed by GeckoTerminal)")
    print(f"  chain registry: {p['pools_total']} pools total, {p['impostors']} impostor tokens")


if __name__ == "__main__":
    main()
