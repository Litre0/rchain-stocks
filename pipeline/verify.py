#!/usr/bin/env python3
"""Assertions over the generated data. Run after the pipeline.

The important one is the COVERAGE regression test: GUH/AAPL, CLARUS/AAPL and
PINE/AAPL are all absent from DexScreener's AAPL pair listing. If someone ever
refactors discovery back onto vendor listings, those three vanish and this
fails -- which is the entire point.

    python3 pipeline/verify.py
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chain as C

AAPL = "0xaf3d76f1834a1d425780943c99ea8a608f8a93f9"
GUH_POOL = "0xaf430d97f3dc5f14aa0042e3f398ba288e51707eac001bf8b46dea1327a6e1cb"
ok = fail = 0


def check(name, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  PASS  {name}" + (f"  ({detail})" if detail else ""))
    else:
        fail += 1
        print(f"  FAIL  {name}" + (f"  ({detail})" if detail else ""))


def load_registry():
    """Prefer live pipeline state, fall back to the committed fixture.

    data/ is gitignored, so on a fresh clone data/stocks.json does not exist.
    fixtures/stocks.json is that same 203-token registry, committed precisely
    so the registry assertions still run offline instead of crashing.
    """
    reg = C.load("stocks.json")
    if reg:
        return reg, "data/stocks.json"
    fx = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "fixtures", "stocks.json")
    if os.path.exists(fx):
        with open(fx) as f:
            return json.load(f), "fixtures/stocks.json"
    return None, None


def dashboard_checks():
    """Assertions over the rendered artifact.

    These are the ones that matter on a fresh clone, where dashboard.html is
    committed but raw pipeline state is not.
    """
    dash = os.path.join(os.path.dirname(C.DATA), "dashboard.html")
    if not os.path.exists(dash):
        return
    print("\ndashboard")
    html = open(dash).read()
    check("no fetch() -- data must be inlined for file://", "fetch(" not in html)
    # Scan the pipeline modules, not this file -- verify.py names the header in
    # its own check text, which matched itself.
    hdr = "User" + "-Agent"
    # The modules live beside THIS file, not beside the rendered dashboard --
    # dashboard.html sits at the repo root, whose directory holds no .py at
    # all, so scanning there would pass on an empty set.
    srcdir = os.path.dirname(os.path.abspath(__file__))
    spoofers = [f for f in sorted(os.listdir(srcdir))
                if f.endswith(".py") and f != os.path.basename(__file__)
                and hdr in open(os.path.join(srcdir, f)).read()]
    check("no browser User-Agent spoofing anywhere in the pipeline",
          not spoofers, ", ".join(spoofers) or "clean: Blockscout never touched")
    check("search index present", "tokenList" in html)


def report():
    print(f"\n{ok} passed, {fail} failed")
    sys.exit(1 if fail else 0)


def main():
    reg, reg_src = load_registry()
    pl = C.load("pools.json")
    toks, snap = C.load("tokens.json"), C.load("snapshot.json")

    print("registry")
    if not reg:
        print("  (no registry -- run pipeline/registry.py)")
        dashboard_checks(); report()
    check("exactly 203 stock tokens", reg["count"] == 203,
          f'{reg["count"]} from {reg_src}')
    rows = reg["tokens"]
    check("oldest is WEEK @ 2026-05-27T20:17:41Z",
          rows[0]["symbol"] == "WEEK" and rows[0]["deployed"] == "2026-05-27T20:17:41Z",
          f'{rows[0]["symbol"]} {rows[0]["deployed"]}')
    check("newest is BND @ 2026-07-28T15:11:34Z",
          rows[-1]["symbol"] == "BND" and rows[-1]["deployed"] == "2026-07-28T15:11:34Z",
          f'{rows[-1]["symbol"]} {rows[-1]["deployed"]}')
    by = {r["address"]: r["symbol"] for r in rows}
    for a, s in ((AAPL, "AAPL"), ("0xd0601ce157db5bdc3162bbac2a2c8af5320d9eec", "NVDA"),
                 ("0x322f0929c4625ed5bad873c95208d54e1c003b2d", "TSLA")):
        check(f"{s} resolves", by.get(a) == s, a[:12])
    check("no duplicate symbols in the registry",
          len({r["symbol"] for r in rows}) == 203)

    print("\npool registry")
    if not pl:
        print("  (pools.json missing -- run pipeline/pools.py for the full battery)")
        dashboard_checks(); report()
    pools = pl["pools"]
    kinds = {}
    for p in pools.values():
        kinds[p["kind"]] = kinds.get(p["kind"], 0) + 1
    check("pool count >= 25,416 (the measured lower bound)", pl["count"] >= 25416,
          f'{pl["count"]:,}  by kind {kinds}')
    withpool = {p["stock"] for p in pools.values() if p["stock"]}
    check("at least 196 stock tokens have a pool", len(withpool) >= 196, str(len(withpool)))
    check("no unscanned gaps", not pl.get("gaps"),
          f'{len(pl.get("gaps") or [])} gap(s)')

    import time as _t
    now = _t.time()
    recent_approx = [k for k, p in pools.items()
                     if p.get("created_approx") and p.get("created_ts")
                     and now - p["created_ts"] < 24 * 3600]
    approx = sum(1 for p in pools.values() if p.get("created_approx"))
    check("no pool from the last 24h has an interpolated date",
          not recent_approx,
          f"{approx:,} interpolated overall, {len(recent_approx)} of them recent")

    aapl_pools = [k for k, p in pools.items() if p["stock"] == AAPL]
    check("AAPL has >= 534 pools (vendors see 36)", len(aapl_pools) >= 534, str(len(aapl_pools)))

    print("\ncoverage regression — pools DexScreener's AAPL listing omits")
    check("GUH/AAPL present", GUH_POOL in pools, GUH_POOL[:22])
    if toks:
        tk = toks["tokens"]
        def has(sym):
            return any(p["stock"] == AAPL and (tk.get(p["other"], {}).get("symbol", "")).upper() == sym
                       for p in pools.values())
        check("CLARUS/AAPL present", has("CLARUS"))
        check("PINE/AAPL present", has("PINE"))

        print("\nticker collisions")
        coll = toks.get("collisions") or []
        counts = {}
        for a in coll:
            s = tk[a]["symbol"].upper()
            counts[s] = counts.get(s, 0) + 1
        for sym, want in (("GME", 11), ("NVDA", 9), ("AAPL", 4), ("TSLA", 3)):
            n = counts.get(sym, 0) + 1
            check(f"{sym}: >= {want} tokens claim the ticker", n >= want, f"found {n}")
        check("every collision has exactly one registry token",
              all(sum(1 for r in rows if r["symbol"].upper() == s) == 1 for s in counts))

    if snap:
        print("\nsnapshot")
        p = snap["provenance"]
        check("snapshot has all 203 stocks", len(snap["stocks"]) == 203, str(len(snap["stocks"])))
        check("some pools priced", p["pools_priced"] > 0, str(p["pools_priced"]))
        pr = {r["pool"] for r in snap["pairs"]}
        check("GUH/AAPL made it into the dashboard rows", GUH_POOL in pr)
        # A pool younger than the bucket must report nothing, never 0 --
        # a fabricated 0.00% reads as "flat" when the truth is "no data".
        import time as _t
        now = _t.time()
        bad = [r for r in snap["pairs"]
               if r.get("created_ts") and now - r["created_ts"] < 4 * 3600
               and (r.get("chg") or {}).get("h4") is not None]
        young = [r for r in snap["pairs"]
                 if r.get("created_ts") and now - r["created_ts"] < 4 * 3600]
        check("pools younger than 4h report no 4h value", not bad,
              f"{len(young)} young pools, {len(bad)} wrongly filled")

    dashboard_checks()
    report()


if __name__ == "__main__":
    main()
