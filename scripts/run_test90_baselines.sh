#!/bin/bash
# Final numbers (v2 §3.3, test90 EN K=30, 3510 q): RAG + context baselines, same settings as
# run_v2_baselines_answers.sh (8B :8001, Embedding-8B :8006, Reranker-4B :8007; no new model).
# sliding_window budget = SFT Writer's test90 mean ctx (1019 tokens), equal-token comparison.
# Answers run back to back; a judge loop luna-judges each finished run in order (concurrency 8).
# Resumable (rag_hybrid_rr continues from 447). Log: ~/logs/test90_baselines.log (+ _judge.log)
cd ~/M-locomo
source ~/.config/memeval/secrets.env
export TIKTOKEN_CACHE_DIR=$HOME/.cache/tiktoken
LOG=~/logs/test90_baselines.log
COMMON=(--lang en --pool test90 --K 30 --model Qwen3-8B --base-url http://localhost:8001/v1 --api-key-env ""
        --extra-body '{"chat_template_kwargs":{"enable_thinking":false}}' --workers 16 --no-judge)
R=(--answer-format reasoning --max-answer-tokens 1024)
E8=(embed_base_url=http://localhost:8006/v1 embed_model=Qwen3-Embedding-8B)
RR=(rerank_base_url=http://localhost:8007/v1 rerank_model=Qwen3-Reranker-4B)
T=v2test90_K30
NAMES=(rag_hybrid_rr rag_hybrid rag_dense rag_bm25 sliding_window observation_mask full_context_plain)
judge_loop() {
  for n in "${NAMES[@]}"; do run=${T}_$n
    until grep -q "done -> .*/runs/$run\$" $LOG; do sleep 60; done
    echo "=== judge $run $(date)"
    ~/envs/memeval/bin/python -m memeval.score runs/$run --judge-model gpt-6-luna --base-url ${JUDGE_BASE_URL:-https://api.openai.com/v1} \
        --api-key-env JUDGE_API_KEY --cache .cache/llm.sqlite --workers 8 2>&1 | grep "Judge acc"
  done; echo ALLDONE
}
judge_loop > ~/logs/test90_baselines_judge.log 2>&1 &
run() { name=$1; shift; echo "=== $name $(date)"
  ~/envs/memeval/bin/python -m memeval.run "${COMMON[@]}" --run-name ${T}_$name "$@" 2>&1 | tail -3; }
run rag_hybrid_rr --method rag_hybrid --method-config top_k=10 "${E8[@]}" "${RR[@]}" "${R[@]}"
run rag_hybrid --method rag_hybrid --method-config top_k=10 "${E8[@]}" "${R[@]}"
run rag_dense --method rag_dense --method-config top_k=10 "${E8[@]}" "${R[@]}"
run rag_bm25 --method rag_bm25 --method-config top_k=10 "${R[@]}"
run sliding_window --method sliding_window --method-config window_tokens=1019 "${R[@]}"
run observation_mask --method observation_mask "${R[@]}"
run full_context_plain --method full_context
echo ALLDONE
wait
