#!/usr/bin/env python3
"""Which stock-paired pools are actually trading, from swap logs.

The pool registry has ~25k pools and most are dead, so liveness is its own
tier. It is windowed because swap density is brutal: >10,000 V4 swaps per
5,000 blocks (~1.7M/day), and eth_getLogs caps at 10,000 matched logs, so a
naive 24h sweep is 170+ queries. Default window is 6h.

    python3 pipeline/live.py                # 6h
    python3 pipeline/live.py --window 24h
    python3 pipeline/live.py --window 90m
    python3 pipeline/live.py --window 1h --out live_1h.json
"""
import os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chain as C


def main():
    args = sys.argv[1:]
    win = "6h"
    if "--window" in args:
        win = args[args.index("--window") + 1]
    secs = C.parse_window(win)

    pl = C.load("pools.json")
    if not pl:
        sys.exit("run stocks/pools.py first")
    pools = pl["pools"]

    v4_ids = {k for k, p in pools.items() if p["kind"] == "v4"}
    addr_pools = {k: p for k, p in pools.items() if p["kind"] != "v4"}
    managers = sorted({p["venue"] for p in pools.values() if p["kind"] == "v4"})

    head = C.latest_block()
    now = C.block_time(head)
    start = C.block_at_time(now - secs, head)
    C.log(f"window {win} = blocks {start}..{head} ({head-start:,} blocks)")

    swaps = {}
    last = {}

    def bump(key, blk):
        swaps[key] = swaps.get(key, 0) + 1
        if blk > last.get(key, 0):
            last[key] = blk

    # V4: pool identity is topics[1]. One sweep across all PoolManagers --
    # eth_getLogs accepts an address array, so there is no reason to pay a
    # separate pass per manager.
    def sink_v4(logs):
        for lg in logs:
            pid = lg["topics"][1]
            if pid in v4_ids:
                bump(pid, int(lg["blockNumber"], 16))

    if managers:
        # Measured ~7.2 V4 swaps per block chain-wide against a 10,000-log
        # cap, so ~1.2k blocks is the largest chunk that usually fits.
        # Starting bigger just burns round trips bisecting down to it.
        C.log(f"  v4 swaps across {len(managers)} pool manager(s)")
        C.sweep([C.T_SWAP_V4], start, head, address=managers, chunk=1_200,
                sink=sink_v4, label="swap.v4")

    # V2/V3: pool identity is the emitting address
    for topic, tag in ((C.T_SWAP_V3, "v3"), (C.T_SWAP_V2, "v2")):
        def sink(logs):
            for lg in logs:
                a = lg["address"].lower()
                if a in addr_pools:
                    bump(a, int(lg["blockNumber"], 16))
        C.log(f"  {tag} swaps")
        C.sweep([topic], start, head, chunk=50_000, sink=sink, label=f"swap.{tag}")

    rows = []
    for k, n in swaps.items():
        p = pools[k]
        rows.append({"pool": k, "swaps": n, "last_block": last[k],
                     "kind": p["kind"], "stock": p["stock"],
                     "stock_symbol": p["stock_symbol"], "other": p["other"],
                     "created": p.get("created"), "created_ts": p.get("created_ts")})
    rows.sort(key=lambda r: -r["swaps"])

    out_name = "live.json"
    if "--out" in args:
        out_name = args[args.index("--out") + 1]
    C.save(out_name, {"generated": C.iso(int(time.time())), "window": win,
                         "from_block": start, "head": head,
                         "count": len(rows), "pools": rows})
    print(f"live stock-paired pools in {win}: {len(rows)} of {len(pools)}")
    for r in rows[:10]:
        print(f"  {r['swaps']:>6} swaps  {r['stock_symbol'] or '?':<6} pool {r['pool'][:20]}  {r['created']}")


if __name__ == "__main__":
    main()
