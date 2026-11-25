#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
LG="python3 loadgen/loadgen.py"

docker compose down -v >/dev/null 2>&1
docker compose up -d --build --wait >/dev/null 2>&1
$LG seed --ids results/ids.json
docker info --format '{{.NCPU}} CPU, {{.MemTotal}} bytes' > results/docker_resources.txt

$LG step --path /noop --out results/noop
G=$(python3 -c "import json; print(json.load(open('results/noop_steps.json'))['X'])")
echo "G = $G"

for n in 1 2 3 4; do
  docker compose up -d --scale web=$n --wait >/dev/null 2>&1
  docker compose restart balancer >/dev/null 2>&1
  sleep 10
  $LG step --instances $n --limit "$G" --out results/step_n$n &
  pid=$!
  scripts/sampler.sh $pid results/sampler_n$n.txt
  wait $pid
done
