#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p results
scripts/destroy.sh >/dev/null 2>&1 || true
scripts/deploy.sh > results/deploy.log 2>&1
kubectl get nodes -o wide > results/nodes.txt
python3 loadgen/loadgen.py seed --ids results/ids.json
python3 scripts/lab.py 1 2 3 4 5 6 7 8 9 10 2>&1 | tee results/lab.log
