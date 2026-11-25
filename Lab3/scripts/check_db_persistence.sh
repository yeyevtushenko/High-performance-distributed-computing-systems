#!/usr/bin/env bash
set -euo pipefail
B=http://localhost:8080
echo "\$ curl $B/donations?limit=1"
curl -s "$B/donations?limit=1"; echo
old=$(kubectl get pod db-0 -o jsonpath='{.metadata.uid}')
echo "\$ kubectl delete pod db-0"
kubectl delete pod db-0
until [ "$(kubectl get pod db-0 -o jsonpath='{.metadata.uid}' 2>/dev/null)" != "$old" ] && \
      [ "$(kubectl get pod db-0 -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)" = "True" ]; do
  sleep 1
done
kubectl rollout status deployment/web --timeout=120s >/dev/null
until [ "$(curl -s -o /dev/null -w '%{http_code}' $B/healthz)" = 200 ]; do sleep 1; done
echo "\$ kubectl get pod db-0"
kubectl get pod db-0
echo "\$ curl $B/donations?limit=1"
curl -s "$B/donations?limit=1"; echo
