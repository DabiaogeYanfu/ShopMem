#!/bin/bash
# test90 full_context_plain rerun: the default 120k (tiktoken) budget is ~146k Qwen3 tokens on test90's
# K=30 histories and overflows the 8B's 131072 window (HTTP 400). max_context_tokens=100000 (tiktoken)
# ~= 123-127k Qwen3 tokens, keeping the most recent part of the history. Answers then luna judge.
# Log: ~/logs/test90_fullctx.log
cd ~/M-locomo
source ~/.config/memeval/secrets.env
export TIKTOKEN_CACHE_DIR=$HOME/.cache/tiktoken
NAME=v2test90_K30_full_context_plain
echo "=== $NAME $(date)"
~/envs/memeval/bin/python -m memeval.run --lang en --pool test90 --K 30 --model Qwen3-8B --base-url http://localhost:8001/v1 \
    --api-key-env "" --extra-body '{"chat_template_kwargs":{"enable_thinking":false}}' --workers 16 --no-judge \
    --run-name $NAME --method full_context --method-config max_context_tokens=100000 2>&1 | tail -3
grep -q "done -> .*/runs/$NAME\$" ~/logs/test90_fullctx.log || { echo "answers not done, skip judge"; exit 1; }
echo "=== judge $NAME $(date)"
~/envs/memeval/bin/python -m memeval.score runs/$NAME --judge-model gpt-6-luna --base-url ${JUDGE_BASE_URL:-https://api.openai.com/v1} \
    --api-key-env JUDGE_API_KEY --cache .cache/llm.sqlite --workers 8 2>&1 | grep "Judge acc"
echo ALLDONE
