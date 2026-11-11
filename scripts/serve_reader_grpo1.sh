#!/bin/bash
# Lab rollout service for Reader GRPO run reader_grpo1 (train banks with request units, same as the pool).
# Log: ~/logs/reader_grpo1_serve.log
cd ~/M-locomo
source ~/.config/memeval/secrets.env
export TIKTOKEN_CACHE_DIR=$HOME/.cache/tiktoken
exec ~/envs/memeval/bin/python -m memeval.reader_rl serve --run-name reader_grpo1 \
    --reader-base-url http://localhost:18100/v1 --bank-dir runs/train_K20_banks_req --port 18120 --workers 32 --gpt-workers 8
