#!/bin/bash
# E2 main experiment: eval pool (90 scenarios, 3510 q), K=30, dataset v0.3, Qwen3-8B answers,
# gpt-6-luna judge. Every method uses Qwen3-Embedding-8B; the reasoning answer format for all
# (plus full_context in the plain format, its better one on dev). Resumable: re-running skips
# answered questions. Answers only (--no-judge): judging runs afterwards, at low concurrency, in one mode for all runs.
cd ~/M-locomo
source ~/.config/memeval/secrets.env
export TIKTOKEN_CACHE_DIR=$HOME/.cache/tiktoken
COMMON=(--lang zh --pool eval --K 30 --model Qwen3-8B --base-url http://localhost:8001/v1 --api-key-env ""
        --extra-body "{\"chat_template_kwargs\":{\"enable_thinking\":false}}" --workers 16
        --no-judge)
R=(--answer-format reasoning --max-answer-tokens 1024)
E8=(embed_base_url=http://localhost:8006/v1 embed_model=Qwen3-Embedding-8B)
RR=(rerank_base_url=http://localhost:8007/v1 rerank_model=Qwen3-Reranker-4B)
run() { name=$1; shift; echo "=== $name $(date)"; ~/envs/memeval/bin/python -m memeval.run "${COMMON[@]}" --run-name $name "$@" 2>&1 | tail -3; }
run main_K30_shopmem --method shopmem --method-config "${E8[@]}" "${RR[@]}" "${R[@]}"
run main_K30_rag_hybrid_rr --method rag_hybrid --method-config top_k=10 "${E8[@]}" "${RR[@]}" "${R[@]}"
run main_K30_full_context --method full_context "${R[@]}"
run main_K30_full_context_plain --method full_context
run main_K30_rag_hybrid --method rag_hybrid --method-config top_k=10 "${E8[@]}" "${R[@]}"
run main_K30_rag_dense --method rag_dense --method-config top_k=10 "${E8[@]}" "${R[@]}"
run main_K30_rag_bm25 --method rag_bm25 --method-config top_k=10 "${R[@]}"
run main_K30_observation_mask --method observation_mask "${R[@]}"
run main_K30_simplemem --method simplemem --method-config "${E8[@]}" "${R[@]}"
run main_K30_oracle_gate_rag --method oracle_gate_rag --method-config top_k=10 "${E8[@]}" "${R[@]}"
# sliding window at ShopMem's measured mean context size (equal-token comparison)
BUDGET=$(~/envs/memeval/bin/python -c "import json; r=[json.loads(x) for x in open(\"runs/main_K30_shopmem/predictions.jsonl\")]; print(round(sum(x[\"context_tokens\"] for x in r)/len(r)))")
run main_K30_sliding_window --method sliding_window --method-config window_tokens=$BUDGET "${R[@]}"
echo ALLDONE
