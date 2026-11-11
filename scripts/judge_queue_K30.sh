#!/bin/bash
# Judge the K=30 main runs one at a time as their answers finish, at low concurrency
# (JUDGE_WORKERS, default 4), one question per request -- the same judging as every
# earlier run. Batched judging was rejected: 87% agreement with per-item judging and a
# method-dependent shift (dev: ShopMem +4.6, full_context -1.3).
WORKERS=${JUDGE_WORKERS:-4}
cd ~/M-locomo
source ~/.config/memeval/secrets.env
export TIKTOKEN_CACHE_DIR=$HOME/.cache/tiktoken
for r in shopmem rag_hybrid_rr full_context full_context_plain rag_hybrid rag_dense rag_bm25 observation_mask \
         simplemem oracle_gate_rag sliding_window; do
  run=main_K30_$r
  until grep -q "done -> .*/runs/$run\$" ~/logs/main_K30.log; do sleep 60; done
  echo "=== judge $run $(date)"
  ~/envs/memeval/bin/python -m memeval.score runs/$run --judge-model gpt-6-luna --base-url ${JUDGE_BASE_URL:-https://api.openai.com/v1} \
      --api-key-env JUDGE_API_KEY --cache .cache/llm.sqlite --workers $WORKERS 2>&1 | grep "Judge acc"
done
echo ALLDONE
