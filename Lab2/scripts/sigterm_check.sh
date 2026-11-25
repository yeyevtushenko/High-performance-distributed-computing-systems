#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
docker compose up -d --scale web=2 --wait >/dev/null 2>&1
docker compose restart balancer >/dev/null 2>&1
sleep 5
python3 loadgen/loadgen.py const --rate 500 --duration 40 --instances 2 --out results/sigterm &
pid=$!
sleep 22
C=$(docker compose ps -q web | tail -1)
stop_at=$(python3 -c 'import time; print(round(time.time(), 3))')
docker stop "$C" >/dev/null
wait $pid
echo "stopped container: $(docker inspect --format '{{.Name}}' "$C")"
echo "stop issued at unix $stop_at"
echo "exit code: $(docker inspect --format '{{.State.ExitCode}}' "$C")"
python3 - "$stop_at" <<'EOF'
import csv, sys
stop = float(sys.argv[1])
rows = [r for r in csv.DictReader(open("results/sigterm_seconds.csv")) if abs(float(r["unix"]) + 0.5 - stop) <= 5]
print("unix second sent ok_2xx non_2xx conn_errors")
for r in rows:
    print(r["unix"], r["second"], r["sent"], r["ok_2xx"], r["non_2xx"], r["conn_errors"])
print("total non-2xx:", sum(int(r["non_2xx"]) for r in rows), "connection errors:", sum(int(r["conn_errors"]) for r in rows))
EOF
docker compose up -d --scale web=2 --wait >/dev/null 2>&1
