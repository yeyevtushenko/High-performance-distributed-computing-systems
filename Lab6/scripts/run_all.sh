#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
export GLOO_SOCKET_IFNAME=lo0
TR=".venv/bin/torchrun --nnodes=1 --rdzv-backend=static --master-addr=127.0.0.1 --master-port=29500 --node-rank=0 --nproc_per_node"
mkdir -p results

$PY train.py info --out results/info.json

$PY train.py train --out results/step1_train.json
$PY train.py speed --out results/step1_speed.json

$TR=2 train.py check --out results/step2_check.json
$TR=2 train.py speed --sync manual --out results/step2_manual.json
$TR=2 train.py speed --sync ddp --out results/step2_ddp.json
$TR=2 train.py speed --sync ddp --no-sync --out results/step2_ddp_nosync.json
$TR=2 train.py train --sync ddp --out results/step2_ddp_train.json

$TR=1 train.py speed --sync ddp --out results/step3_p1.json
$TR=1 train.py speed --sync ddp --no-sync --out results/step3_p1_nosync.json

$TR=1 train.py speed --sync ddp --batch 96 --out results/step4_p1.json
$TR=1 train.py train --sync ddp --batch 96 --out results/step4_p1_train.json
$TR=2 train.py train --sync ddp --lr 0.04 --out results/step4_p2_lr_scaled_train.json

rm -f results/ckpt.pt results/fail.marker results/step5_events.jsonl
.venv/bin/torchrun --nnodes=1 --nproc_per_node=2 --max-restarts=1 \
  --rdzv-backend=static --master-addr=127.0.0.1 --master-port=29500 --node-rank=0 \
  train.py train --sync ddp --ckpt results/ckpt.pt --ckpt-every 15 --fail-step 37 \
  --marker results/fail.marker --events results/step5_events.jsonl --out results/step5_train.json
$PY summarize.py
