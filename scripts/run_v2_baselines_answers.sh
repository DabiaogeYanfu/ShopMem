#!/bin/bash
# v2 experiment chapter, answers only (--no-judge), run while the GPUs are idle during teacher
# scoring: uses only the already-running services on our GPUs 3-6 (Qwen3-8B :8001, Embedding-8B
# :8006, Reranker-4B :8007); loads no new model. Data v0.5d (EN, frozen). Judging runs later,
# per-item, once luna is free (teacher scoring holds GPT concurrency 8). Resumable.
#   1) test90 EN K=30 main-table baselines (settings as run_main_K30_answers.sh)
#   2) dev30 EN K=30 v1 full vs drop_tools (motivation observation 3; dev only, not test)
# Deferred: sliding_window (budget = the Writer's ctx tokens), Mem-T/Mem-a and the Writer (new models).
# Log: ~/logs/v2_baselines_answers.log
cd ~/M-locomo
source ~/.config/memeval/secrets.env
export TIKTOKEN_CACHE_DIR=$HOME/.cache/tiktoken
COMMON=(--lang en --K 30 --model Qwen3-8B --base-url http://localhost:8001/v1 --api-key-env ""
        --extra-body "{\"chat_template_kwargs\":{\"enable_thinking\":false}}" --workers 16
        --no-judge ${EXTRA:-})
R=(--answer-format reasoning --max-answer-tokens 1024)
E8=(embed_base_url=http://localhost:8006/v1 embed_model=Qwen3-Embedding-8B)
RR=(rerank_base_url=http://localhost:8007/v1 rerank_model=Qwen3-Reranker-4B)
P=${PREFIX:-v2}
run() { pool=$1 name=$2; shift 2; echo "=== $name $(date)"
  ~/envs/memeval/bin/python -m memeval.run "${COMMON[@]}" --pool $pool --run-name $name "$@" 2>&1 | tail -3; }
T=${P}test90_K30
run test90 ${T}_shopmem_v1 --method shopmem --method-config "${E8[@]}" "${RR[@]}" "${R[@]}"
run test90 ${T}_rag_hybrid_rr --method rag_hybrid --method-config top_k=10 "${E8[@]}" "${RR[@]}" "${R[@]}"
run test90 ${T}_full_context_plain --method full_context
run test90 ${T}_rag_hybrid --method rag_hybrid --method-config top_k=10 "${E8[@]}" "${R[@]}"
run test90 ${T}_rag_dense --method rag_dense --method-config top_k=10 "${E8[@]}" "${R[@]}"
run test90 ${T}_rag_bm25 --method rag_bm25 --method-config top_k=10 "${R[@]}"
run test90 ${T}_observation_mask --method observation_mask "${R[@]}"
D=${P}dev30_K30
run dev30 ${D}_shopmem_v1 --method shopmem --method-config "${E8[@]}" "${RR[@]}" "${R[@]}"
run dev30 ${D}_shopmem_v1_drop_tools --method shopmem --method-config "${E8[@]}" "${RR[@]}" gate=drop_tools "${R[@]}"
run test90 ${T}_full_context --method full_context "${R[@]}"   # appendix only
run test90 ${T}_simplemem --method simplemem --method-config "${E8[@]}" "${R[@]}"   # slowest, last
echo ALLDONE
