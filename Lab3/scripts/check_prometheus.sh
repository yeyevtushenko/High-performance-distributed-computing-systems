#!/usr/bin/env bash
set -euo pipefail
q() {
  echo "\$ query: $1"
  curl -s http://localhost:9090/api/v1/query --data-urlencode "query=$1" | python3 -c '
import json, sys
for r in json.load(sys.stdin)["data"]["result"]:
    print(" ", {k: v for k, v in r["metric"].items() if k in ("__name__", "pod", "node", "container", "deployment", "job")}, r["value"][1])'
}
q 'sum by (pod) (rate(http_requests_total[1m]))'
q 'container_cpu_cfs_periods_total{container="web"}'
q 'container_cpu_cfs_throttled_periods_total{container="web"}'
q 'kube_deployment_status_replicas_ready{deployment="web"}'
q 'pg_up'
echo "\$ kubectl top pods"
kubectl top pods
