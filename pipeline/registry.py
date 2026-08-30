#!/usr/bin/env python3
"""Enumerate every tokenized stock on Robinhood Chain, from the chain itself.

All stock tokens are BeaconProxies minted by one factory
(0x4783C6...C046) which emits (address token, string name, string symbol) on
each deployment. That event is the ONLY complete enumeration: OZ 5.x dropped
BeaconUpgraded, so there are no proxy-side logs to scan.

The factory is chain-wide, not launchpad-scoped -- launchpads (Pons, Hoodit,
Clanker...) mint memecoins and merely *select* a stock token as a pool's quote.

    python3 pipeline/registry.py            -> data/stocks.json
    python3 pipeline/registry.py --show 10
"""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chain as C


def build():
    head = C.latest_block()
    C.log(f"sweeping stock factory {C.STOCK_FACTORY} to block {head}")
    logs, gaps = C.sweep([C.T_STOCK_NEW], 0, head, address=C.STOCK_FACTORY,
                         chunk=5_000_000, label="registry")
    rows = []
    for lg in logs:
        b = bytes.fromhex(lg["data"][2:])
        rows.append({
            "address": "0x" + b[12:32].hex(),
            "name": C.dec_string_at(b, 32),
            "symbol": C.dec_string_at(b, 64),
            "block": int(lg["blockNumber"], 16),
            "tx": lg["transactionHash"],
        })
    rows.sort(key=lambda r: r["block"])
    C.log(f"{len(rows)} stock tokens; resolving deploy timestamps")
    ts = C.block_times([r["block"] for r in rows])
    for r in rows:
        t = ts.get(r["block"])
        r["deployed_ts"] = t
        r["deployed"] = C.iso(t) if t else None
    out = {"generated": C.iso(int(__import__("time").time())),
           "head": head, "gaps": gaps, "count": len(rows), "tokens": rows}
    C.save("stocks.json", out)
    return out


def main():
    args = sys.argv[1:]
    if "--show" in args:
        n = int(args[args.index("--show") + 1])
        d = C.load("stocks.json") or build()
        rows = d["tokens"]
        for r in rows[:n]:
            print(f"{r['deployed']}  {r['symbol']:<7} {r['name'][:44]:<44} {r['address']}")
        print(f"... {len(rows)} total")
        return
    d = build()
    rows = d["tokens"]
    print(f"stock tokens: {len(rows)}")
    if rows:
        print(f"oldest: {rows[0]['symbol']:<6} {rows[0]['deployed']}  {rows[0]['name']}")
        print(f"newest: {rows[-1]['symbol']:<6} {rows[-1]['deployed']}  {rows[-1]['name']}")
    if d["gaps"]:
        print(f"WARNING: {len(d['gaps'])} unscanned range(s) -- count is a lower bound")


if __name__ == "__main__":
    main()
