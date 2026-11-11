#!/bin/bash
# TOPR round 1, correct version (topr1c): same SFT-ep3 rollouts + luna rewards as topr1/topr1b, but flat
# windows (zero reward spread) skipped (--min-spread 1e-6) and shift 0.05 (36% positives). Lab GPU 7.
cd ~/M-locomo
RUN=topr1c_train_K20
mkdir -p runs/$RUN
for f in config.json rewards.jsonl samples generate_stats.json; do ln -sfn ../topr1_train_K20/$f runs/$RUN/$f; done
~/envs/memeval/bin/python -m memeval.topr_data --run-name $RUN --baseline-shift 0.05 --min-spread 1e-6 > runs/$RUN/topr_data.log 2>&1
mkdir -p runs/topr1c_writer
CUDA_VISIBLE_DEVICES=7 ~/envs/sft/bin/python scripts/train_writer_topr.py --data runs/$RUN/topr \
    --init-adapter runs/sft_writer_K20/adapter_ep3 --out runs/topr1c_writer --model models/Qwen3-4B \
    --epochs 1 --save-every 50 > runs/topr1c_writer/train.log 2>&1
echo "ALLDONE $(date)" >> runs/topr1c_writer/train.log
