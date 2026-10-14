#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
LG="python3 loadgen/loadgen.py"
RATES=${RATES:-"10 100 500 5000"}
NOOP_RATES=${NOOP_RATES-$RATES}

docker compose down -v >/dev/null 2>&1
docker compose up -d --scale web=1 --wait >/dev/null 2>&1
$LG seed --ids results/ids.json
for r in $NOOP_RATES; do
  $LG open --path /noop --rate $r --out results/noop_r${r}.json
done

for r in $RATES; do
  for rep in 1 2 3; do
    for n in 1 2; do
      docker compose up -d --scale web=$n --wait >/dev/null 2>&1
      since=$(date -u +%Y-%m-%dT%H:%M:%SZ)
      $LG open --rate $r --instances $n --out results/open_n${n}_r${r}_${rep}.json
      if [ $n = 2 ]; then
        docker compose logs --no-log-prefix --since "$since" balancer \
          | grep '/donations/' | awk '{print $(NF-1)}' | sort | uniq -c > results/distribution_r${r}_${rep}.txt
      fi
    done
  done
done
