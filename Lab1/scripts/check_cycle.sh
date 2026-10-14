#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.."
B=http://localhost:8080
J='Content-Type: application/json'

step() {
  echo
  echo "\$ $*"
  "$@" -s -w '\nHTTP %{http_code}\n'
}

docker compose down -v >/dev/null 2>&1
docker compose up -d --wait >/dev/null 2>&1

step curl -X POST $B/donations -H "$J" -d '{"donor_code":"D-7001","blood_group":"A","rh_factor":"+","volume_ml":450,"donation_date":"2026-10-12","is_suitable":true}'
step curl -X POST $B/donations -H "$J" -d '{"donor_code":"D-7002","blood_group":"C","rh_factor":"+","volume_ml":150,"donation_date":"2026-10-12","is_suitable":true}'
step curl "$B/donations?blood_group=A&limit=10&offset=0"
step curl $B/donations/1
step curl -X PUT $B/donations/1 -H "$J" -d '{"donor_code":"D-7001","blood_group":"A","rh_factor":"-","volume_ml":400,"donation_date":"2026-10-12","is_suitable":false}'
step curl -X PUT $B/donations/999 -H "$J" -d '{"donor_code":"D-7001","blood_group":"A","rh_factor":"-","volume_ml":400,"donation_date":"2026-10-12","is_suitable":false}'
step curl $B/donations/1
step curl -X POST $B/donations -H "$J" -d '{"donor_code":"D-7003","blood_group":"O","rh_factor":"-","volume_ml":300,"donation_date":"2026-10-13","is_suitable":true}'

echo
echo "\$ docker compose down && docker compose up -d --wait"
docker compose down >/dev/null 2>&1
docker compose up -d --wait >/dev/null 2>&1
step curl $B/donations

step curl -X DELETE $B/donations/1
step curl -X DELETE $B/donations/1
step curl $B/donations/1

echo
echo "\$ docker compose down -v && docker compose up -d --wait"
docker compose down -v >/dev/null 2>&1
docker compose up -d --wait >/dev/null 2>&1
step curl $B/healthz
step curl $B/donations
