#!/usr/bin/env python3
"""Resolve tickers for every token ever paired with a stock, and date the fakes.

Tickers are NOT unique on Robinhood Chain. Many tokens impersonate a registry
ticker with deliberately confusable names ("Apple Inc. Common Stock"). Exactly
one of each is issued by the stock factory, so registry membership is the
proof of which is real; age only corroborates it.

Three things here are shaped by measurement, not preference:

  * symbol() is read for all ~27k counterparties but name() only for the ones
    that turn out to collide -- names are only displayed for impostors, and
    fetching both doubles the slowest step in the pipeline.
  * Deploy dates come from ONE chain-wide sweep of mint Transfers
    (from == 0x0) filtered to the collision set via an address array, not a
    per-token log search. Per-token was measured at ~7 tokens/min: 3.7 hours
    for 1,633 tokens.
  * Progress is checkpointed, because this step is long enough that losing it
    to a failure at the end is unacceptable.

    python3 pipeline/symbols.py
    python3 pipeline/symbols.py --no-dating
"""
import os, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chain as C

SEL_SYMBOL, SEL_NAME = "0x95d89b41", "0x06fdde03"
ZERO_TOPIC = "0x" + "0" * 64
BATCH = 400            # C.batch re-chunks to the 25-per-request RPC limit


def read_calls(addrs, selector, label, checkpoint=None):
    out = {}
    for i in range(0, len(addrs), BATCH):
        part = addrs[i:i + BATCH]
        res = C.batch([("eth_call", [{"to": a, "data": selector}, "latest"])
                       for a in part])
        for a, r in zip(part, res):
            out[a] = C.dec_abi_string(r) if r else ""
        done = min(i + BATCH, len(addrs))
        if (i // BATCH) % 5 == 0 or done == len(addrs):
            C.log(f"  {label} {done:,}/{len(addrs):,}")
        if checkpoint and (i // BATCH) % 5 == 4:
            checkpoint(out)
    return out


def sweep_mints(addrs):
    """Earliest mint (Transfer from 0x0) per token, in a handful of queries."""
    first = {}
    CH = 300           # addresses per address-array filter
    for i in range(0, len(addrs), CH):
        part = addrs[i:i + CH]
        logs, gaps = C.sweep([C.T_TRANSFER, ZERO_TOPIC], 0, C.latest_block(),
                             address=part, chunk=10_000_000, label="mints")
        for lg in logs:
            a = lg["address"].lower()
            b = int(lg["blockNumber"], 16)
            if a not in first or b < first[a][0]:
                first[a] = (b, lg["transactionHash"])
        C.log(f"  mints {min(i + CH, len(addrs)):,}/{len(addrs):,} "
              f"({len(first):,} dated)")
    return first


def main():
    reg = C.load("stocks.json")
    pl = C.load("pools.json")
    if not reg or not pl:
        sys.exit("run stocks/registry.py and stocks/pools.py first")

    registry = {t["address"]: t for t in reg["tokens"]}
    earliest = {}
    for p in pl["pools"].values():
        for a in (p["token0"], p["token1"]):
            if a not in earliest or p["block"] < earliest[a]:
                earliest[a] = p["block"]
    universe = sorted(set(earliest) | set(registry))

    prev = (C.load("tokens.json") or {}).get("tokens", {})
    tokens = {a: dict(prev.get(a, {}), address=a) for a in universe}
    for a, r in registry.items():
        tokens[a].update(symbol=r["symbol"], name=r["name"], registry=True,
                         deployed=r["deployed"], deployed_ts=r["deployed_ts"])
    for a in universe:
        tokens[a].setdefault("registry", False)
        tokens[a]["first_pool_block"] = earliest.get(a)

    def save(partial=False, collisions=()):
        C.save("tokens.json", {"generated": C.iso(int(time.time())),
                               "partial": partial, "count": len(tokens),
                               "collisions": list(collisions), "tokens": tokens})

    # ---- 1. symbols for everything not already cached ----
    todo = [a for a in universe if not tokens[a].get("registry")
            and not tokens[a].get("symbol_done")]
    C.log(f"{len(universe):,} tokens; {len(todo):,} need symbol()")
    if todo:
        def ck(partial):
            for a, sym in partial.items():
                tokens[a]["symbol"] = sym
                tokens[a]["symbol_done"] = True
            save(partial=True)
        syms = read_calls(todo, SEL_SYMBOL, "symbol", checkpoint=ck)
        for a, sym in syms.items():
            tokens[a]["symbol"] = sym
            tokens[a]["symbol_done"] = True
        save(partial=True)

    # ---- 2. the collision set ----
    tickers = {t["symbol"].upper() for t in reg["tokens"]}
    collisions = sorted(a for a, t in tokens.items()
                        if not t["registry"] and (t.get("symbol") or "").upper() in tickers)
    C.log(f"collision set: {len(collisions):,} tokens impersonate a registry ticker")

    # ---- 3. names, only where they are displayed ----
    need_name = [a for a in collisions if not tokens[a].get("name")]
    if need_name:
        names = read_calls(need_name, SEL_NAME, "name")
        for a, n in names.items():
            tokens[a]["name"] = n
    save(partial=True, collisions=collisions)

    # ---- 4. date them, and find who minted them ----
    if "--no-dating" not in sys.argv:
        need = [a for a in collisions if not tokens[a].get("deployed_ts")]
        C.log(f"dating {len(need):,} impostors via one mint sweep")
        first = sweep_mints(need)
        blocks = [b for b, _ in first.values()]
        ts = C.block_times(blocks, stride=4)
        for a, (b, tx) in first.items():
            if ts.get(b):
                tokens[a]["deployed_ts"] = ts[b]
                tokens[a]["deployed"] = C.iso(ts[b])
        C.log(f"resolving {len(first):,} deployers")
        items = list(first.items())
        for i in range(0, len(items), 200):
            part = items[i:i + 200]
            res = C.batch([("eth_getTransactionByHash", [tx]) for _, (_, tx) in part])
            for (a, _), t in zip(part, res):
                if t and t.get("from"):
                    tokens[a]["deployer"] = t["from"].lower()
            C.log(f"  deployers {min(i + 200, len(items)):,}/{len(items):,}")

    save(partial=False, collisions=collisions)

    counts = {}
    for a in collisions:
        s = tokens[a]["symbol"].upper()
        counts[s] = counts.get(s, 0) + 1
    print(f"tokens resolved: {len(tokens):,}")
    print(f"impostors claiming a registry ticker: {len(collisions):,}")
    print("  most-impersonated tickers (count includes the real one):")
    for s, n in sorted(counts.items(), key=lambda kv: -kv[1])[:10]:
        print(f"    {s:<7} {n + 1}")


if __name__ == "__main__":
    main()
