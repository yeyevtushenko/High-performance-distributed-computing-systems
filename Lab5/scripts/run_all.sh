#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PY=python3
$PY bench.py info --out results/info.json
for sync in global rw; do
  for i in 1 2 3; do
    $PY bench.py run --sync $sync --out results/run_${sync}_$i.json
    $PY bench.py x1 --sync $sync --out results/x1_${sync}_$i.json
  done
done
for i in 1 2 3; do
  $PY bench.py tcs --out results/tcs_$i.json
done
$PY bench.py readonly --threads 6 --duration 3 --out results/readonly.json
$PY summarize.py
