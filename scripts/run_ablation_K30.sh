#!/bin/bash
# E4 ablations (实验计划 4.5): ShopMem v9 at the main-experiment setting (eval pool, K=30,
# dataset v0.3, Qwen3-8B write + answer, Embedding-8B, Reranker-4B, reasoning format), one
# switch changed per run. The full model is main_K30_shopmem. Answers only (--no-judge);
# scripts/judge_queue_ablation_K30.sh judges each run after it finishes. Resumable.
# SMOKE=1: 1 history, 1 question per category, runs named smoke_abl_*, no judge queue.
cd ~/M-locomo
source ~/.config/memeval/secrets.env
export TIKTOKEN_CACHE_DIR=$HOME/.cache/tiktoken
COMMON=(--lang zh --pool eval --K 30 --model Qwen3-8B --base-url http://localhost:8001/v1 --api-key-env ""
        --extra-body "{\"chat_template_kwargs\":{\"enable_thinking\":false}}" --workers 16
        --no-judge --answer-format reasoning --max-answer-tokens 1024 --method shopmem)
BASE=(embed_base_url=http://localhost:8006/v1 embed_model=Qwen3-Embedding-8B
      rerank_base_url=http://localhost:8007/v1 rerank_model=Qwen3-Reranker-4B)
PREFIX=abl_K30
if [ "$SMOKE" = 1 ]; then COMMON+=(--max-histories 1 --limit-per-category 1); PREFIX=smoke_abl_K30; fi
run() { name=${PREFIX}_$1; shift; echo "=== $name $(date)"
        ~/envs/memeval/bin/python -m memeval.run "${COMMON[@]}" --run-name $name --method-config "${BASE[@]}" "$@" 2>&1 | tail -3; }
# B. industrial changes (N1-N6)
run n1a_keep_all      gate=keep_all
run n1b_drop_tools    gate=drop_tools
run n2_no_entity_norm entity_norm=false
run n3_no_shift       shift=false
run n4_no_sources     synth_sources=false
run n5_shared_depth   depth=shared
run n6a_no_cliff      cliff=false
run n6b_k_min1        k_min=1
# A. inherited modules
run a_no_synth        synth=false
run a_fixed_k         depth=fixed
# C. retrieval views
run c_sem             views=sem
run c_lex             views=lex
run c_sym             views=sym
run c_sem_lex         views=sem,lex
run c_sem_sym         views=sem,sym
run c_lex_sym         views=lex,sym
echo ALLDONE
