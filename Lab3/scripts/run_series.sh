#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
LG="python3 loadgen/loadgen.py"
R=5

wait_ready() {
  kubectl rollout status deployment/web --timeout=300s >/dev/null
  until [ "$(kubectl get deployment web -o jsonpath='{.status.readyReplicas}')" = "$1" ]; do sleep 2; done
  sleep 15
}

for n in 1 2 3 $R; do
  kubectl scale deployment/web --replicas=$n
  wait_ready $n
  kubectl get pods -l app=web -o wide --no-headers | awk '{print $1, $7}' > results/pods_n$n.txt
  $LG step --instances $n --out results/step_n$n
done

python3 - <<'EOF' > results/elastic_profile.txt
import json
x1 = json.load(open("results/step_n1_steps.json"))["X"]
xr = json.load(open("results/step_n5_steps.json"))["X"]
spike = min(2 * x1, 0.9 * xr)
print(f"30:{0.3 * x1:.0f},60:{spike:.0f},90:{0.3 * x1:.0f}")
EOF
PROFILE=$(cat results/elastic_profile.txt)

kubectl scale deployment/web --replicas=$R
wait_ready $R
$LG segments --instances $R --profile "$PROFILE" --out results/fixed

kubectl apply -f k8s/hpa.yaml
ones=0
while [ $ones -lt 60 ]; do
  cur=$(kubectl get hpa web -o jsonpath='{.status.currentReplicas}')
  ready=$(kubectl get deployment web -o jsonpath='{.status.readyReplicas}')
  if [ "$cur" = "1" ] && [ "$ready" = "1" ]; then ones=$((ones + 5)); else ones=0; fi
  sleep 5
done
kubectl get hpa web > results/hpa_before.txt
$LG segments --instances 1 --profile "$PROFILE" --out results/hpa
kubectl get hpa web > results/hpa_after.txt
kubectl delete -f k8s/hpa.yaml
