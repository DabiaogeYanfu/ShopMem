#!/bin/bash
# E4-A "w/o semantic compression": ShopMem with compress=false (each window stored verbatim as
# one entry; no extraction, gate or synthesis), otherwise the main-experiment config. Queued
# after scripts/run_ablation_K30.sh, and judged after its judge queue, so the judge stays at
# JUDGE_WORKERS (default 4) concurrent requests.
WORKERS=${JUDGE_WORKERS:-4}
cd ~/M-locomo
source ~/.config/memeval/secrets.env
export TIKTOKEN_CACHE_DIR=$HOME/.cache/tiktoken
run=abl_K30_a_no_compress
until grep -q ALLDONE ~/logs/ablation_K30.log; do sleep 60; done
echo "=== $run $(date)"
~/envs/memeval/bin/python -m memeval.run --lang zh --pool eval --K 30 --model Qwen3-8B \
    --base-url http://localhost:8001/v1 --api-key-env "" \
    --extra-body "{\"chat_template_kwargs\":{\"enable_thinking\":false}}" --workers 16 \
    --no-judge --answer-format reasoning --max-answer-tokens 1024 --method shopmem --run-name $run \
    --method-config embed_base_url=http://localhost:8006/v1 embed_model=Qwen3-Embedding-8B \
    rerank_base_url=http://localhost:8007/v1 rerank_model=Qwen3-Reranker-4B compress=false 2>&1 | tail -3
until grep -q ALLDONE ~/logs/judge_ablation_K30.log; do sleep 60; done
echo "=== judge $run $(date)"
~/envs/memeval/bin/python -m memeval.score runs/$run --judge-model gpt-6-luna --base-url ${JUDGE_BASE_URL:-https://api.openai.com/v1} \
    --api-key-env JUDGE_API_KEY --cache .cache/llm.sqlite --workers $WORKERS 2>&1 | grep "Judge acc"
echo ALLDONE
