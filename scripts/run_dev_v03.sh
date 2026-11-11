#!/bin/bash
# dataset v0.3 sanity check on dev (390q, K=10): ShopMem v9 final, rag_hybrid + rerank, full_context (both answer formats)
cd ~/M-locomo
source ~/.config/memeval/secrets.env
export TIKTOKEN_CACHE_DIR=$HOME/.cache/tiktoken
COMMON=(--lang zh --pool dev --K 10 --model Qwen3-8B --base-url http://localhost:8001/v1 --api-key-env ""
        --extra-body "{\"chat_template_kwargs\":{\"enable_thinking\":false}}"
        --judge-model gpt-6-luna --judge-base-url ${JUDGE_BASE_URL:-https://api.openai.com/v1} --judge-api-key-env JUDGE_API_KEY)
R=(--answer-format reasoning --max-answer-tokens 1024)
E8=(embed_base_url=http://localhost:8006/v1 embed_model=Qwen3-Embedding-8B)
RR=(rerank_base_url=http://localhost:8007/v1 rerank_model=Qwen3-Reranker-4B)
run() { name=$1; shift; echo "=== $name $(date)"; ~/envs/memeval/bin/python -m memeval.run "${COMMON[@]}" --run-name $name "$@" 2>&1 | tail -3; }
run v03dev_shopmem_R --method shopmem --method-config "${E8[@]}" "${RR[@]}" "${R[@]}"
run v03dev_rag_hybrid_rr_R --method rag_hybrid --method-config top_k=10 "${E8[@]}" "${RR[@]}" "${R[@]}"
run v03dev_full_context --method full_context
run v03dev_full_context_R --method full_context "${R[@]}"
echo ALLDONE
