#!/usr/bin/env python3
"""Error-aware RPC layer for Robinhood Chain sweeps.

A naive RPC wrapper that swallows JSON-RPC errors is fine for one-shot lookups
but useless for sweeping: we need to tell a 10k-log cap from a query timeout
from a 429, because each wants a different response.

  cap / timeout -> bisect the block range
  429           -> back off and retry the SAME range (bisecting makes it worse)

Stdlib only. Uses curl subprocess for HTTP, matching the rest of the repo.
"""
import json, os, subprocess, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from keccak import keccak256, topic0  # noqa: E402,F401

RPC = os.environ.get("RH_RPC", "https://rpc.mainnet.chain.robinhood.com")

HERE = os.path.dirname(os.path.abspath(__file__))
# Artifacts live at the repo root, not beside the code, so the site and the
# CI workflow can publish them without reaching into pipeline/.
DATA = os.environ.get("RH_DATA") or os.path.join(os.path.dirname(HERE), "data")
os.makedirs(DATA, exist_ok=True)

# ---------- topics ----------
T_PAIR_CREATED = topic0("PairCreated(address,address,address,uint256)")
T_POOL_CREATED = topic0("PoolCreated(address,address,uint24,int24,address)")
T_INITIALIZE   = topic0("Initialize(bytes32,address,address,uint24,int24,address,uint160,int24)")
T_SWAP_V4      = topic0("Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)")
T_SWAP_V3      = topic0("Swap(address,address,int256,int256,uint160,uint128,int24)")
T_SWAP_V2      = topic0("Swap(address,uint256,uint256,uint256,uint256,address)")
T_TRANSFER     = topic0("Transfer(address,address,uint256)")
# Stock-token factory: emits StockDeployed-style (address token, string name, string symbol)
STOCK_FACTORY  = "0x4783C67b63dE2B358Ac5951a7D41F47A38F3C046"
T_STOCK_NEW    = "0xd9b0c6a1c0de228715ad0fa09f3259686ee84f8cc675e03ef7e47a9cdafa76d6"

MAX_LOGS = 10000            # server-side cap, observed

# Quote assets, not memecoins. A stock pool whose other leg is one of these is
# the stock's OWN market (AAPL/USDG), not a memecoin launch quoted in a stock.
# Of 1,535 live stock-paired pools, 300 are USDG, 81 native ETH and 32 WETH --
# enough to bury every meme in the table if they are not separated out.
USDG  = "0x5fc5360d0400a0fd4f2af552add042d716f1d168"
WETH  = "0x0bd7d308f8e1639fab988df18a8011f41eacad73"
NATIVE = "0x" + "0" * 40
MAJORS = {USDG, WETH, NATIVE}


def is_meme_pair(p):
    """True when the non-stock leg is a memecoin rather than a quote asset."""
    return (not p.get("both_stock") and p.get("stock")
            and p.get("other") not in MAJORS)
VERBOSE = os.environ.get("RH_QUIET") != "1"


def log(msg):
    if VERBOSE:
        print(msg, file=sys.stderr, flush=True)


# ---------- transport ----------

PACE = float(os.environ.get("RH_PACE", "0.10"))   # measured optimum: at 0.10 all
                                                 # chunks succeed at ~38 good calls/s; at 0.0
                                                 # the limiter rejects 18/32 and yields 16.7/s
_last_req = [0.0]


def _post(body, timeout):
    gap = PACE - (time.time() - _last_req[0])
    if gap > 0:
        time.sleep(gap)
    _last_req[0] = time.time()
    return subprocess.run(
        ["curl", "-s", "-m", str(timeout), "-X", "POST", RPC,
         "-H", "Content-Type: application/json", "-d", body],
        capture_output=True, text=True).stdout


def call(method, params, timeout=60):
    """Returns (result, error_kind). error_kind in {None,'toomany','timeout','rate','net','rpc'}."""
    body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1})
    out = _post(body, timeout)
    if not out.strip():
        return None, "net"
    try:
        d = json.loads(out)
    except Exception:
        return None, "net"
    if "error" in d:
        msg = str(d["error"].get("message", "")).lower()
        code = d["error"].get("code")
        if "exceeds limit" in msg or "more than" in msg:
            return None, "toomany"
        if "timed out" in msg or "timeout" in msg:
            return None, "timeout"
        if code == 429 or "too many requests" in msg:
            return None, "rate"
        return None, "rpc"
    return d.get("result"), None


def rpc(method, params, tries=4, timeout=60):
    """Single call with retry on transient failure. Raises on persistent error."""
    delay = 1.0
    for _ in range(tries):
        res, err = call(method, params, timeout)
        if err is None:
            return res
        if err in ("rate", "net"):
            time.sleep(delay)
            delay = min(delay * 2, 20)
            continue
        raise RuntimeError(f"{method} failed: {err}")
    raise RuntimeError(f"{method} failed after {tries} tries (last={err})")


MAX_BATCH = 25          # the RPC counts each sub-request against its rate
                        # limit: batches of 25 succeed, 50+ return a blanket
                        # {"error":{"code":429}} for the WHOLE batch -- which
                        # looks like N silent nulls if you don't check for it.


def _batch_once(calls, timeout):
    """One JSON-RPC array request. Returns (results|None, error_kind)."""
    body = json.dumps([{"jsonrpc": "2.0", "method": m, "params": p, "id": i}
                       for i, (m, p) in enumerate(calls)])
    out = _post(body, timeout)
    try:
        d = json.loads(out)
    except Exception:
        return None, "net"
    if isinstance(d, dict):                       # whole-batch rejection
        err = d.get("error") or {}
        msg = str(err.get("message", "")).lower()
        return None, ("rate" if err.get("code") == 429 or "too many" in msg else "rpc")
    res = [None] * len(calls)
    for item in d:
        i = item.get("id")
        if isinstance(i, int) and 0 <= i < len(calls):
            res[i] = item.get("result")
    return res, None


def batch(calls, timeout=90, tries=5, size=MAX_BATCH):
    """calls = [(method, params), ...] -> [result|None, ...] in order.

    Chunks to a size the rate limiter accepts and backs off on 429. A chunk
    that keeps failing falls back to single calls, so a throttled stretch
    degrades in speed rather than silently returning nulls.
    """
    out = []
    for i in range(0, len(calls), size):
        part = calls[i:i + size]
        delay, got = 1.0, None
        for _ in range(tries):
            got, err = _batch_once(part, timeout)
            if err is None:
                break
            time.sleep(delay)
            delay = min(delay * 2, 30)
        if got is None:
            got = []
            for m, p in part:
                try:
                    got.append(rpc(m, p, tries=3, timeout=timeout))
                except Exception:
                    got.append(None)
        out.extend(got)
    return out


def latest_block():
    return int(rpc("eth_blockNumber", []), 16)


# ---------- adaptive log sweep ----------

def sweep(topics, frm, to, address=None, chunk=None, sink=None, label=""):
    """Sweep eth_getLogs over [frm,to], bisecting on cap/timeout, backing off on 429.

    sink(logs) is called per successful chunk; if None, logs are accumulated.
    Returns (logs_or_None, gaps) where gaps = [(lo,hi), ...] ranges that never succeeded.
    """
    acc = [] if sink is None else None
    gaps = []
    span = to - frm + 1
    chunk = chunk or max(1, min(span, 10_000_000))
    pending = []
    b = frm
    while b <= to:
        e = min(b + chunk - 1, to)
        pending.append((b, e))
        b = e + 1
    pending.reverse()   # use as a stack, ascending order preserved on pop

    done = 0
    while pending:
        lo, hi = pending.pop()
        params = {"fromBlock": hex(lo), "toBlock": hex(hi), "topics": topics}
        if address:
            params["address"] = address
        delay = 2.0
        while True:
            res, err = call("eth_getLogs", [params], timeout=120)
            if err != "rate":
                break
            time.sleep(delay)
            delay = min(delay * 2, 60)
        if err is None:
            if sink is not None:
                sink(res)
            else:
                acc.extend(res)
            done += hi - lo + 1
            if len(res) >= MAX_LOGS:
                log(f"  {label} [{lo},{hi}] returned {len(res)} == cap; treating as suspect")
            continue
        if err in ("toomany", "timeout", "net", "rpc") and hi > lo:
            mid = (lo + hi) // 2
            pending.append((mid + 1, hi))
            pending.append((lo, mid))
            continue
        gaps.append((lo, hi))
        log(f"  {label} GAP [{lo},{hi}] err={err}")
    if gaps:
        log(f"  {label} finished with {len(gaps)} gap(s)")
    return (acc, gaps)


# ---------- block time ----------

_TS = {}


def block_time(bn):
    if bn in _TS:
        return _TS[bn]
    blk = rpc("eth_getBlockByNumber", [hex(bn), False])
    ts = int(blk["timestamp"], 16)
    _TS[bn] = ts
    return ts


def block_times(bns, stride=1, exact_from=None):
    """Timestamps for many blocks.

    stride>1 fetches every Nth block exactly and linearly interpolates the
    rest between its bracketing anchors. Block time is locally linear even
    though the chain-wide rate is not (measured: interpolating across a 25k
    block window is off by 0.0-0.5s in the modern chain and at most ~67s in
    the earliest blocks), so this trades a bounded, documented error for a
    large cut in requests against a rate limiter that throttles hard.

    exact_from: never interpolate at or above this block, so recent pools --
    where age is displayed in minutes -- stay exact.
    """
    uniq = sorted({b for b in bns if b not in _TS})
    if not uniq:
        return {b: _TS.get(b) for b in bns}

    if stride > 1:
        want = set(uniq[::stride]) | {uniq[0], uniq[-1]}
        if exact_from is not None:
            want |= {b for b in uniq if b >= exact_from}
    else:
        want = set(uniq)
    todo = sorted(want)
    log(f"  timestamps: {len(todo):,} exact lookups for {len(uniq):,} blocks"
        + (f" (stride {stride})" if stride > 1 else ""))

    STEP = 500
    for i in range(0, len(todo), STEP):
        part = todo[i:i + STEP]
        res = batch([("eth_getBlockByNumber", [hex(b), False]) for b in part])
        miss = 0
        for b, r in zip(part, res):
            if r and r.get("timestamp"):
                _TS[b] = int(r["timestamp"], 16)
            else:
                miss += 1
        log(f"  timestamps {min(i + STEP, len(todo)):,}/{len(todo):,}"
            + (f"  ({miss} unresolved)" if miss else ""))

    if stride > 1:
        anchors = sorted(b for b in todo if b in _TS)
        approx = set()
        if anchors:
            import bisect as _b
            for blk in uniq:
                if blk in _TS:
                    continue
                j = _b.bisect_left(anchors, blk)
                lo = anchors[j - 1] if j > 0 else anchors[0]
                hi = anchors[j] if j < len(anchors) else anchors[-1]
                if hi == lo:
                    _TS[blk] = _TS[lo]
                else:
                    frac = (blk - lo) / (hi - lo)
                    _TS[blk] = int(_TS[lo] + (_TS[hi] - _TS[lo]) * frac)
                approx.add(blk)
        if approx:
            log(f"  {len(approx):,} timestamps interpolated between anchors")
        block_times.approx = approx
    else:
        block_times.approx = set()
    return {b: _TS.get(b) for b in bns}


def block_at_time(target_ts, head=None):
    """Binary-search the first block at/after target_ts.

    Block rate on RC is wildly non-uniform (~4/s early, ~10/s now), so never
    extrapolate from a constant -- search real timestamps.
    """
    lo, hi = 1, head or latest_block()
    if block_time(hi) <= target_ts:
        return hi
    while lo < hi:
        mid = (lo + hi) // 2
        if block_time(mid) < target_ts:
            lo = mid + 1
        else:
            hi = mid
    return lo


def iso(ts):
    import datetime
    return datetime.datetime.fromtimestamp(ts, datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_window(s):
    """'6h' / '90m' / '2d' -> seconds."""
    s = str(s).strip().lower()
    mult = {"m": 60, "h": 3600, "d": 86400}
    if s and s[-1] in mult:
        return int(float(s[:-1]) * mult[s[-1]])
    return int(float(s))


# ---------- abi bits ----------

def addr_of_topic(t):
    return "0x" + t[-40:].lower()


def dec_string_at(b, off):
    o = int.from_bytes(b[off:off + 32], "big")
    ln = int.from_bytes(b[o:o + 32], "big")
    return b[o + 32:o + 32 + ln].decode("utf-8", "replace")


def dec_abi_string(hexdata):
    """Decode a single dynamic string return value; '' on anything unexpected."""
    try:
        b = bytes.fromhex(hexdata[2:])
        if len(b) < 64:
            # some tokens return bytes32
            return b.rstrip(b"\x00").decode("utf-8", "replace")
        return dec_string_at(b, 0)
    except Exception:
        return ""


def load(name, default=None):
    p = os.path.join(DATA, name)
    if not os.path.exists(p):
        return default
    with open(p) as f:
        return json.load(f)


def save(name, obj):
    p = os.path.join(DATA, name)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, p)
    return p
