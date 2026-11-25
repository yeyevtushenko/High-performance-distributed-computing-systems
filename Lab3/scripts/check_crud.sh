#!/usr/bin/env bash
set -uo pipefail
B=http://localhost:8080
J='Content-Type: application/json'

step() {
  echo
  echo "\$ $*"
  "$@" -s -D - | grep -iE '^(HTTP/|x-pod:)|^\{' | tr -d '\r'
  echo
}

step curl -X POST $B/donations -H "$J" -d '{"donor_code":"D-9001","blood_group":"B","rh_factor":"+","volume_ml":450,"donation_date":"2026-11-20","is_suitable":true}'
ID=$(curl -s "$B/donations?limit=1&offset=100" | python3 -c "import json,sys; print(json.load(sys.stdin)['items'][0]['id'])")
step curl "$B/donations?blood_group=B&limit=2&offset=0"
step curl $B/donations/$ID
step curl -X PUT $B/donations/$ID -H "$J" -d '{"donor_code":"D-9001","blood_group":"B","rh_factor":"-","volume_ml":400,"donation_date":"2026-11-20","is_suitable":false}'
step curl $B/donations/$ID
step curl -X DELETE $B/donations/$ID
step curl -X DELETE $B/donations/$ID
step curl $B/donations/$ID
