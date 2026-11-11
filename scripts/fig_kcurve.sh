#!/bin/bash
# K-curve figure (paper item 4): judge accuracy vs scenarios interleaved per history K, test90 EN, 3510 q
# at every K. Runs from scripts/run_kcurve.sh (K=1/10/90) + the K=30 main table. full_context_plain is
# truncated to its 100k-token budget at K=30 (2340/3510 q) and K=90 (all q) -> open markers.
#   bash scripts/fig_kcurve.sh    -> results/kcurve/acc_vs_K.{pdf,png,csv,meta.json}
cd ~/M-locomo
R=(); for m in grpo_stab1_final rag_hybrid_rr full_context_plain; do for K in 1 10 30 90; do R+=(runs/v2test90_K${K}_$m); done; done
~/envs/memeval/bin/python -m memeval.figures curve --runs "${R[@]}" --x K --logx --metric judge_acc_micro \
    --xlabel 'Scenarios interleaved per history, K (log scale)' \
    --label 'runs/v2test90_K30_grpo_stab1_final=ShopMem (ours)' \
    --label 'runs/v2test90_K30_rag_hybrid_rr=RAG hybrid+rerank' \
    --label 'runs/v2test90_K30_full_context_plain=Full context' \
    --hollow runs/v2test90_K30_full_context_plain runs/v2test90_K90_full_context_plain \
    --hollow-label 'Context truncated to 100k tokens' \
    --out results/kcurve/acc_vs_K
