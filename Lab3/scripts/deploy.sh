#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
CLUSTER=lab3

k3d cluster create "$CLUSTER" --servers 1 --agents 2 \
  --k3s-arg "--node-taint=node-role.kubernetes.io/control-plane=true:NoSchedule@server:0" \
  --k3s-arg "--disable=traefik@server:0" \
  -p "8080:30080@server:0" -p "9090:30090@server:0" --wait
kubectl label node "k3d-$CLUSTER-server-0" role=infra --overwrite

docker build -t donations-api:3.0 .
k3d image import donations-api:3.0 -c "$CLUSTER"

kubectl create secret generic db-secret --from-env-file=.env --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f k8s/postgres.yaml
kubectl rollout status statefulset/db --timeout=180s
kubectl apply -f k8s/migrate.yaml
kubectl wait --for=condition=complete job/migrate --timeout=180s
kubectl apply -f k8s/web.yaml
kubectl rollout status deployment/web --timeout=180s

helm upgrade --install monitoring prometheus-community/kube-prometheus-stack --version 93.10.1 \
  -f k8s/monitoring/kube-prometheus-stack.yaml --wait --timeout 10m
helm upgrade --install pg-exporter prometheus-community/prometheus-postgres-exporter --version 8.3.0 \
  -f k8s/monitoring/postgres-exporter.yaml --wait --timeout 5m
