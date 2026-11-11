#!/bin/bash
cd ~/M-locomo
source ~/.config/memeval/secrets.env
for run in dev30_writer_topr1c_step200_rule dev30_writer_topr1c_final_rule; do
  echo "=== judge $run $(date)"
  ~/envs/memeval/bin/python -m memeval.score runs/$run --judge-model gpt-6-luna --base-url ${JUDGE_BASE_URL:-https://api.openai.com/v1} \
      --api-key-env JUDGE_API_KEY --cache .cache/llm.sqlite --workers 8 2>&1 | grep "Judge acc"
done; echo ALLDONE
