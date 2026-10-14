#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
URL=http://localhost:8080/healthz
POLL=$(mktemp)
docker compose up -d --scale web=1 --wait >/dev/null 2>&1
C=$(docker compose ps -q web)
echo "restart policy: $(docker inspect -f '{{.HostConfig.RestartPolicy.Name}}' "$C")"
echo "before: restart count $(docker inspect -f '{{.RestartCount}}' "$C"), pid $(docker inspect -f '{{.State.Pid}}' "$C"), started $(docker inspect -f '{{.State.StartedAt}}' "$C")"

python3 - "$URL" "$POLL" <<'EOF' &
import sys, time, urllib.request
url, out = sys.argv[1], sys.argv[2]
end = time.time() + 15
with open(out, "w") as f:
    while time.time() < end:
        try:
            code = urllib.request.urlopen(url, timeout=1).status
        except Exception as e:
            code = getattr(e, "code", 0)
        f.write(f"{time.time():.3f} {code}\n")
        time.sleep(0.02)
EOF
since=$(date +%s)
sleep 1
docker run --rm --pid=host alpine:3.20 kill -9 "$(docker inspect -f '{{.State.Pid}}' "$C")"
wait

echo "after: restart count $(docker inspect -f '{{.RestartCount}}' "$C"), pid $(docker inspect -f '{{.State.Pid}}' "$C"), started $(docker inspect -f '{{.State.StartedAt}}' "$C")"
EVENTS=$(docker events --since "$since" --until "$(date +%s)" --filter container="$C" \
  --format '{{.TimeNano}} {{.Action}} exitCode={{index .Actor.Attributes "exitCode"}}' | grep -E ' (die|start) ')
echo "$EVENTS" | awk '{print $2, $3}'
python3 - "$POLL" "$EVENTS" <<'EOF'
import sys
die = next(int(l.split()[0]) / 1e9 for l in sys.argv[2].splitlines() if " die " in l)
polls = [(float(t), int(c)) for t, c in (l.split() for l in open(sys.argv[1]))]
failed = [t for t, c in polls if t > die and c != 200]
up = next(t for t, c in polls if t > die and c == 200)
print(f"non-200 responses after kill: {len(failed)}")
print(f"time from process death to first 200 on /healthz: {up - die:.2f} s")
EOF
rm -f "$POLL"
