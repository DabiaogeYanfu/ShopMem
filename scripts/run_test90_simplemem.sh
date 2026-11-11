#!/bin/bash
# Final numbers (v2 §3.3, test90 EN K=30, 3510 q): SimpleMem, official defaults (memeval/methods/
# simplemem_adapter.py, db80b6a), same settings as main_K30_simplemem. Runs on a second, identical
# Qwen3-8B (GPUs 0,1 :8011, same vLLM args as :8001, free tonight) so the lab's main 8B stays free
# for the ablations; Embedding-8B :8006 shared. Answers, then per-item luna judge (concurrency 8).
# Resumable. Log: ~/logs/test90_simplemem.log
cd ~/M-locomo
source ~/.config/memeval/secrets.env
export TIKTOKEN_CACHE_DIR=$HOME/.cache/tiktoken
NAME=v2test90_K30_simplemem
echo "=== $NAME $(date)"
~/envs/memeval/bin/python -m memeval.run --lang en --pool test90 --K 30 --model Qwen3-8B --base-url http://localhost:8011/v1 \
    --api-key-env "" --extra-body '{"chat_template_kwargs":{"enable_thinking":false}}' --workers 16 --no-judge \
    --answer-format reasoning --max-answer-tokens 1024 --run-name $NAME --method simplemem \
    --method-config embed_base_url=http://localhost:8006/v1 embed_model=Qwen3-Embedding-8B 2>&1 | tail -3
grep -q "done -> .*/runs/$NAME\$" ~/logs/test90_simplemem.log || { echo "answers not done, skip judge"; exit 1; }
echo "=== judge $NAME $(date)"
~/envs/memeval/bin/python -m memeval.score runs/$NAME --judge-model gpt-6-luna --base-url ${JUDGE_BASE_URL:-https://api.openai.com/v1} \
    --api-key-env JUDGE_API_KEY --cache .cache/llm.sqlite --workers 8 2>&1 | grep "Judge acc"
echo ALLDONE
