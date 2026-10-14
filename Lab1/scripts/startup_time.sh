#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
URL=http://localhost:8080/healthz
OUT=results/startup_time.txt
: > "$OUT"

for i in 1 2 3; do
  docker compose down -v >/dev/null 2>&1
  t0=$(python3 -c 'import time; print(time.time())')
  docker compose up -d >/dev/null 2>&1 &
  until [ "$(curl -s -o /dev/null -w '%{http_code}' "$URL" || true)" = "200" ]; do sleep 0.05; done
  t1=$(python3 -c 'import time; print(time.time())')
  wait
  python3 -c "print(f'run $i: {$t1 - $t0:.2f} s')" | tee -a "$OUT"
done

python3 - "$OUT" <<'EOF' | tee -a "$OUT"
import re, statistics, sys
values = [float(m) for m in re.findall(r": ([\d.]+) s", open(sys.argv[1]).read())]
print(f"median: {statistics.median(values):.2f} s")
EOF
docker image inspect donations-api:1.0 --format 'image size: {{.Size}} bytes' | tee -a "$OUT"
