#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
RATE=${RATE:-5000}
C=$(python3 - "$RATE" <<'EOF'
import glob, json, statistics, sys
runs = [json.load(open(p)) for p in glob.glob(f"results/open_n1_r{sys.argv[1]}_*.json")]
print(round(statistics.median(r["mean_sent_in_flight"] for r in runs)))
EOF
)
echo "concurrency from open loop at $RATE rps: $C"
docker compose down -v >/dev/null 2>&1
docker compose up -d --scale web=1 --wait >/dev/null 2>&1
python3 loadgen/loadgen.py seed --ids results/ids.json
python3 loadgen/loadgen.py closed --concurrency "$C" --instances 1 --out results/closed_n1_c${C}.json
