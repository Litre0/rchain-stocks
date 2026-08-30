#!/usr/bin/env python3
"""Every pool on Robinhood Chain with a tokenized stock as one of its legs.

Discovery is chain-derived on purpose. Vendor per-TOKEN pair listings are hard
capped (DexScreener 30, GeckoTerminal 20) and are NOT sorted by liquidity, so
they silently drop live pools -- DexScreener's AAPL listing omits GUH/AAPL
($92k) and CLARUS/AAPL ($118k) while including $0 entries. Against 534 AAPL
pools on chain, the two vendors together see 36. So: enumerate here, price
later (vendors are a pricing service, never a discovery service).

Both indexed leg slots are swept for each event, because token ordering is by
address sort -- a stock can be token0 or token1.

    python3 pipeline/pools.py              # incremental (resumes from cursor)
    python3 pipeline/pools.py --full       # rescan from genesis
    python3 pipeline/pools.py --retry-gaps # re-attempt ranges that previously failed
    python3 pipeline/pools.py --exact-dates # no timestamp interpolation (much slower)
"""
import os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chain as C

# event -> (topic0, [leg slot indices into topics])
EVENTS = {
    "v2":  (C.T_PAIR_CREATED, [1, 2]),
    "v3":  (C.T_POOL_CREATED, [1, 2]),
    "v4":  (C.T_INITIALIZE,   [2, 3]),
}


def pool_key(kind, lg):
    """Stable pool identity: V4 pools are a bytes32 id, V2/V3 an address."""
    if kind == "v4":
        return lg["topics"][1]
    data = lg["data"][2:]
    if kind == "v2":                      # (address pair, uint)
        return "0x" + data[24:64].lower()
    return "0x" + data[88:128].lower()    # v3: (int24, address pool)


def sweep_range(stocks, frm, to):
    """-> (rows dict keyed by pool, gaps list)."""
    padded = ["0x" + "0" * 24 + a[2:] for a in stocks]
    rows, gaps = {}, []
    for kind, (t0, slots) in EVENTS.items():
        for slot in slots:
            topics = [t0] + [None] * (slot - 1) + [padded]
            C.log(f"  sweep {kind} slot{slot} [{frm},{to}]")
            found = {"n": 0}

            def sink(logs, kind=kind, slot=slot, found=found):
                for lg in logs:
                    k = pool_key(kind, lg)
                    if k in rows:
                        continue
                    tp = lg["topics"]
                    legs = ([C.addr_of_topic(tp[1]), C.addr_of_topic(tp[2])] if kind != "v4"
                            else [C.addr_of_topic(tp[2]), C.addr_of_topic(tp[3])])
                    rows[k] = {
                        "pool": k, "kind": kind, "venue": lg["address"].lower(),
                        "token0": legs[0], "token1": legs[1],
                        "block": int(lg["blockNumber"], 16),
                    }
                    found["n"] += 1

            _, g = C.sweep(topics, frm, to, chunk=5_000_000, sink=sink,
                           label=f"{kind}.slot{slot}")
            C.log(f"    +{found['n']} pools")
            gaps += [{"event": kind, "slot": slot, "from": lo, "to": hi} for lo, hi in g]
    return rows, gaps


def main():
    args = sys.argv[1:]
    reg = C.load("stocks.json")
    if not reg:
        sys.exit("run stocks/registry.py first")
    stocks = {t["address"]: t["symbol"] for t in reg["tokens"]}

    prev = C.load("pools.json") or {"pools": {}, "scanned_to": -1, "gaps": []}
    head = C.latest_block()
    full = "--full" in args
    start = 0 if full or prev["scanned_to"] < 0 else prev["scanned_to"] + 1

    pools = {} if full else dict(prev["pools"])
    gaps = [] if full else list(prev["gaps"])

    if "--retry-gaps" in args and gaps:
        C.log(f"retrying {len(gaps)} previous gap(s)")
        still = []
        for g in gaps:
            r, g2 = sweep_range(stocks, g["from"], g["to"])
            for k, v in r.items():
                pools.setdefault(k, v)
            still += g2
        gaps = still

    if start <= head:
        C.log(f"sweeping blocks {start}..{head}")
        t0 = time.time()
        rows, g = sweep_range(stocks, start, head)
        for k, v in rows.items():
            pools.setdefault(k, v)
        gaps += g
        C.log(f"sweep done in {time.time()-t0:.0f}s")

    # classify legs + resolve creation timestamps for new pools
    for p in pools.values():
        s0, s1 = p["token0"] in stocks, p["token1"] in stocks
        p["stock"] = p["token0"] if s0 else (p["token1"] if s1 else None)
        p["other"] = p["token1"] if s0 else p["token0"]
        p["stock_symbol"] = stocks.get(p["stock"])
        p["both_stock"] = bool(s0 and s1)

    need = [p["block"] for p in pools.values() if not p.get("created")]
    if need:
        C.log(f"resolving {len(set(need)):,} creation timestamps")
        # The RPC returns blockTimestamp=0x0 on logs, so headers must be
        # fetched -- and it throttles hard under sustained load. Anchor every
        # Nth block and interpolate between, except within ~24h of head where
        # ages are shown in minutes and must be exact.
        stride = 1 if "--exact-dates" in args else 8
        exact_from = max(0, head - 900_000)
        ts = C.block_times(need, stride=stride, exact_from=exact_from)
        approx = getattr(C.block_times, "approx", set())
        for p in pools.values():
            if not p.get("created"):
                t = ts.get(p["block"])
                if t:
                    p["created_ts"] = t
                    p["created"] = C.iso(t)
                    if p["block"] in approx:
                        p["created_approx"] = True

    out = {"generated": C.iso(int(time.time())), "scanned_to": head,
           "gaps": gaps, "count": len(pools), "pools": pools}
    C.save("pools.json", out)

    by_kind = {}
    for p in pools.values():
        by_kind[p["kind"]] = by_kind.get(p["kind"], 0) + 1
    others = {p["other"] for p in pools.values()}
    with_pool = {p["stock"] for p in pools.values() if p["stock"]}
    print(f"pools with a stock leg: {len(pools)}")
    print(f"  by venue kind: {by_kind}")
    print(f"  distinct non-stock counterparties: {len(others)}")
    print(f"  stock tokens with >=1 pool: {len(with_pool)} of {len(stocks)}")
    print(f"  stock/stock pools: {sum(1 for p in pools.values() if p['both_stock'])}")
    if gaps:
        print(f"WARNING: {len(gaps)} unscanned range(s) -- count is a LOWER BOUND."
              f" Re-run with --retry-gaps.")


if __name__ == "__main__":
    main()
