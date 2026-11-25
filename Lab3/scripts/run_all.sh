#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
scripts/destroy.sh >/dev/null 2>&1 || true
scripts/deploy.sh > results/deploy.log 2>&1
kubectl get nodes -o wide > results/nodes.txt
kubectl get pods -o wide > results/pods_after_deploy.txt
python3 loadgen/loadgen.py seed --ids results/ids.json
scripts/check_crud.sh > results/check_crud.txt 2>&1
scripts/check_db_persistence.sh > results/check_db_persistence.txt 2>&1
scripts/check_prometheus.sh > results/check_prometheus.txt 2>&1
kubectl top pods > results/kubectl_top.txt
scripts/run_series.sh > results/series.log 2>&1
