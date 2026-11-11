#!/bin/bash
# Judge the E4 ablation runs one at a time as their answers finish: per-item judging at
# JUDGE_WORKERS (default 4), the same judging as the main K=30 runs.
WORKERS=${JUDGE_WORKERS:-4}
cd ~/M-locomo
source ~/.config/memeval/secrets.env
export TIKTOKEN_CACHE_DIR=$HOME/.cache/tiktoken
for r in n1a_keep_all n1b_drop_tools n2_no_entity_norm n3_no_shift n4_no_sources n5_shared_depth n6a_no_cliff \
         n6b_k_min1 a_no_synth a_fixed_k c_sem c_lex c_sym c_sem_lex c_sem_sym c_lex_sym; do
  run=abl_K30_$r
  until grep -q "done -> .*/runs/$run\$" ~/logs/ablation_K30.log; do sleep 60; done
  echo "=== judge $run $(date)"
  ~/envs/memeval/bin/python -m memeval.score runs/$run --judge-model gpt-6-luna --base-url ${JUDGE_BASE_URL:-https://api.openai.com/v1} \
      --api-key-env JUDGE_API_KEY --cache .cache/llm.sqlite --workers $WORKERS 2>&1 | grep "Judge acc"
done
echo ALLDONE
