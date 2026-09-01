#!/usr/bin/env bash
# Refresh dashboard.html from the chain.
#
# Runs the pipeline stages in dependency order, because each reads what the
# previous one wrote. Detects whether this is a cold start (no data/, i.e. a
# fresh clone) and runs the extra discovery stages only then.
#
#   ./refresh.sh            # normal: pick up new pools, re-price, re-render
#   ./refresh.sh --quick    # prices only; skips pool/ticker discovery
#   ./refresh.sh --full     # re-sweep pool discovery from genesis
#   ./refresh.sh --window 24h
#   ./refresh.sh --dry-run  # print the stages that would run, do nothing
#
# Python 3 standard library only. No API keys. Safe to re-run after an
# interrupt: pools.py and symbols.py checkpoint their progress.
set -u -o pipefail

cd "$(dirname "$(readlink -f "$0")")"

WINDOW=6h; MODE=normal; DRY=no
while [ $# -gt 0 ]; do
  case "$1" in
    --quick)  MODE=quick;;
    --full)   MODE=full;;
    --dry-run) DRY=yes;;
    --window) shift; WINDOW="${1:-6h}";;
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *) echo "unknown option: $1" >&2; exit 2;;
  esac
  shift
done

command -v python3 >/dev/null || { echo "python3 not found. Install Python 3." >&2; exit 1; }

# A cold start is a fresh clone: data/ is gitignored, so it does not exist yet.
COLD=no
for f in stocks.json pools.json tokens.json; do
  [ -f "data/$f" ] || COLD=yes
done

STAGES=()
if [ "$COLD" = yes ]; then
  echo "cold start: no pipeline state found, running full discovery."
  echo "discovery is a one-off: pools.py and symbols.py checkpoint, so later runs skip"
  echo "them. the liveness sweep and pricing do NOT cache -- they run in full every time."
  STAGES=("registry.py" "pools.py" "symbols.py")
elif [ "$MODE" = full ]; then
  STAGES=("registry.py" "pools.py --full" "symbols.py")
elif [ "$MODE" = quick ]; then
  STAGES=()
else
  # Incremental discovery: this is what surfaces pools launched since last run.
  STAGES=("pools.py" "symbols.py")
fi
STAGES+=("live.py --window $WINDOW" "collect.py" "render.py")

# Print what this run will cost BEFORE spending it. live.py keeps no cursor and
# re-sweeps its whole window every run, and collect.py then prices whatever the
# sweep found, so the window governs both. Only the 6h path has been measured;
# everything else is scaled from it and labelled as an estimate.
estimate() {
  case "$WINDOW" in
    30m) sweep="~4 min";  price="~5-10 min";  tag="estimated";;
    1h)  sweep="~8 min";  price="~10-15 min"; tag="estimated";;
    6h)  sweep="~46 min"; price="~40 min";    tag="measured";;
    24h) sweep="~3 hours"; price="longer still"; tag="estimated";;
    *)   sweep="?";       price="?";          tag="unknown window";;
  esac
  echo
  echo "window $WINDOW -> liveness sweep $sweep, then pricing $price  ($tag)"
  if [ "$COLD" = yes ]; then
    echo "plus one-off discovery this run only: pools.py ~27 min, symbols.py ~17 min"
    echo "a full cold build at the 6h default measured 2h 10m end to end."
  fi
  if [ "$WINDOW" = 6h ]; then
    echo
    echo "6h is the default because it matches the shipped snapshot, but it is the"
    echo "slow path. For a routine refresh:  ./refresh.sh --window 1h"
  fi
  echo "nothing here is hung when quiet -- the sweeps print little while they work."
  echo
}

fail() {
  echo
  echo "FAILED at: pipeline/$1"
  case "$1" in
    pools.py*)   echo "  If it warned about unscanned ranges, the count is a lower bound."
                 echo "  Re-run:  python3 pipeline/pools.py --retry-gaps";;
    collect.py*) echo "  Usually GeckoTerminal rate limiting (free tier ~30 req/min)."
                 echo "  Wait a few minutes and re-run. Do NOT raise the request rate.";;
    live.py*)    echo "  Usually an RPC 429. Wait and re-run; try a shorter --window.";;
    render.py*)  echo "  Needs data/snapshot.json -- run pipeline/collect.py first.";;
  esac
  echo "  Nothing is corrupted: every stage checkpoints, so re-running resumes."
  exit 1
}

if [ "$DRY" = yes ]; then
  echo "cold=$COLD mode=$MODE window=$WINDOW"
  estimate
  echo "would run:"
  for stage in "${STAGES[@]}"; do echo "  python3 pipeline/$stage"; done
  echo "  python3 pipeline/verify.py"
  exit 0
fi

estimate

total_start=$(date +%s)
for stage in "${STAGES[@]}"; do
  echo
  echo ">>> pipeline/$stage"
  start=$(date +%s)
  # shellcheck disable=SC2086
  python3 pipeline/$stage || fail "$stage"
  echo "    (${stage%% *} took $(( $(date +%s) - start ))s)"
done

echo
echo "verifying..."
python3 pipeline/verify.py || { echo; echo "verify.py reported failures -- see above."; exit 1; }

echo
echo "done in $(( $(date +%s) - total_start ))s. Reload dashboard.html in your browser:"
echo "  file://$(pwd)/dashboard.html"
