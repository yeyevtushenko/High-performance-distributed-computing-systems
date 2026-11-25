#!/usr/bin/env bash
set -uo pipefail
PID=$1
OUT=$2
: > "$OUT"
while kill -0 "$PID" 2>/dev/null; do
  ts=$(date +%s)
  docker stats --no-stream --format "$ts {{.Name}} {{.CPUPerc}} {{.MemUsage}}" >> "$OUT"
  pids=$(pgrep -P "$PID" | paste -sd, -)
  ps -o %cpu=,rss= -p "$PID${pids:+,$pids}" | awk -v ts="$ts" '{c += $1; m += $2} END {printf "%s loadgen %.1f%% %.1fMiB\n", ts, c, m / 1024}' >> "$OUT"
  top -l 2 -n 0 -s 1 | grep 'CPU usage' | tail -1 | sed "s/^/$ts host /" >> "$OUT"
  sleep 2
done
