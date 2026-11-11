#!/bin/bash
# Full dev set (390 q, K=10), Qwen3-8B answers, gpt-6-luna judge; plain vs reasoning answer format.
cd ~/M-locomo
source ~/.config/memeval/secrets.env
export TIKTOKEN_CACHE_DIR=$HOME/.cache/tiktoken
COMMON=(--lang zh --pool dev --K 10 --model Qwen3-8B --base-url http://localhost:8001/v1 --api-key-env ""
        --extra-body "{\"chat_template_kwargs\":{\"enable_thinking\":false}}"
        --judge-model gpt-6-luna --judge-base-url ${JUDGE_BASE_URL:-https://api.openai.com/v1} --judge-api-key-env JUDGE_API_KEY)
run() { name=$1; shift; echo "=== $name $(date)"; ~/envs/memeval/bin/python -m memeval.run "${COMMON[@]}" --run-name $name "$@" 2>&1 | tail -3; }
run dev390_shopmem --method shopmem
run dev390_rag_hybrid --method rag_hybrid --method-config top_k=10
run dev390_shopmem_reflect --method shopmem --method-config reflect=true
run dev390_full_context --method full_context
run dev390_shopmem_R --method shopmem --answer-format reasoning --max-answer-tokens 1024
run dev390_rag_hybrid_R --method rag_hybrid --method-config top_k=10 --answer-format reasoning --max-answer-tokens 1024
run dev390_full_context_R --method full_context --answer-format reasoning --max-answer-tokens 1024
echo ALLDONE
