#!/bin/bash
# TOPR round 1 redo (topr1b): shift 0.02 data (37% positives), adapter every 50 steps.
cd /root/autodl-tmp/M-locomo
source /root/autodl-tmp/env.sh
mkdir -p runs/topr1b_writer
python scripts/train_writer_topr.py --data runs/topr1b_train_K20/topr --init-adapter runs/sft_writer_K20/adapter_ep3 \
    --out runs/topr1b_writer --model /root/autodl-tmp/models/Qwen3-4B --epochs 1 --save-every 50 \
    > runs/topr1b_writer/train.log 2>&1
echo "ALLDONE $(date)" >> runs/topr1b_writer/train.log
