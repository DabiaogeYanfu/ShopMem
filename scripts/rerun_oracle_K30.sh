#!/bin/bash
# Re-run oracle_gate_rag at K=30 after the per-history gold fix (the first run read other
# histories' low-density labels). Answers now (local 8B only); judging waits until
# judge_retry_K30.sh is done, so only one judge job ever runs (GPT concurrency 4).
cd ~/M-locomo
source ~/.config/memeval/secrets.env
export TIKTOKEN_CACHE_DIR=$HOME/.cache/tiktoken
PY=~/envs/memeval/bin/python
[ -d runs/main_K30_oracle_gate_rag ] && mv runs/main_K30_oracle_gate_rag runs/main_K30_oracle_gate_rag_BUGGY_gold
echo "=== answer $(date)"
$PY -m memeval.run --lang zh --pool eval --K 30 --model Qwen3-8B --base-url http://localhost:8001/v1 --api-key-env "" \
    --extra-body '{"chat_template_kwargs":{"enable_thinking":false}}' --workers 16 --no-judge \
    --run-name main_K30_oracle_gate_rag --method oracle_gate_rag \
    --method-config top_k=10 embed_base_url=http://localhost:8006/v1 embed_model=Qwen3-Embedding-8B \
    --answer-format reasoning --max-answer-tokens 1024 2>&1 | tail -2
while ! grep -q ALLDONE ~/logs/judge_retry_K30.log; do sleep 60; done
echo "=== judge $(date)"
$PY -m memeval.score runs/main_K30_oracle_gate_rag --judge-model gpt-6-luna --base-url ${JUDGE_BASE_URL:-https://api.openai.com/v1} \
    --api-key-env JUDGE_API_KEY --cache .cache/llm.sqlite --workers 4 2>&1 | grep "Judge acc"
echo ALLDONE
