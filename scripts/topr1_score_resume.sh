#!/bin/bash
# Resume luna scoring of topr1_train_K20 only (generation is done; skips scored rows).
cd ~/M-locomo
source ~/.config/memeval/secrets.env
export TIKTOKEN_CACHE_DIR=$HOME/.cache/tiktoken
echo "=== score topr1_train_K20 RESUME 2 (after lab reboot) $(date)"
~/envs/memeval/bin/python -m memeval.teacher score --run-name topr1_train_K20 --answerer luna --row-workers 8 2>&1 | tail -3
echo "ALLDONE $(date)"
