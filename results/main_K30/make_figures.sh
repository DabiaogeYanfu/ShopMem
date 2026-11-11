#!/bin/bash
# E2 main-experiment figures (K=30, eval pool, v0.3). Run from the M-locomo root with the harness env.
set -euo pipefail
PY=${PY:-.venv/bin/python}
R=runs
LABELS=(--label main_K30_full_context_plain="Full context"
        --label main_K30_full_context="Full context (reasoning)"
        --label main_K30_rag_hybrid_rr="RAG-hybrid + rerank"
        --label main_K30_sliding_window="Sliding window"
        --label main_K30_oracle_gate_rag="Oracle gate + RAG")

# accuracy vs context tokens, every run
$PY -m memeval.figures pareto --runs $R/main_K30_shopmem $R/main_K30_rag_hybrid_rr $R/main_K30_rag_hybrid \
    $R/main_K30_rag_dense $R/main_K30_rag_bm25 $R/main_K30_full_context_plain $R/main_K30_full_context \
    $R/main_K30_observation_mask $R/main_K30_sliding_window $R/main_K30_simplemem $R/main_K30_oracle_gate_rag \
    "${LABELS[@]}" --out results/main_K30/pareto_acc_vs_tokens

# per-category accuracy, the five runs the text discusses
$PY -m memeval.figures category --runs $R/main_K30_shopmem $R/main_K30_simplemem $R/main_K30_rag_hybrid_rr \
    $R/main_K30_full_context_plain $R/main_K30_oracle_gate_rag \
    "${LABELS[@]}" --metric judge_acc --out results/main_K30/acc_by_category

# per-category event-level evidence recall, same runs
$PY -m memeval.figures category --runs $R/main_K30_shopmem $R/main_K30_simplemem $R/main_K30_rag_hybrid_rr \
    $R/main_K30_full_context_plain $R/main_K30_oracle_gate_rag \
    "${LABELS[@]}" --metric event_recall --out results/main_K30/recall_by_category
