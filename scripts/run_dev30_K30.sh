#!/bin/bash
# Dev check of the K=30 ablation findings (drop_tools +3.5, no_synth +2.3 on eval): same
# settings as run_ablation_K30.sh but on the held-out dev30 pool (dev + dev_ext, 1 history,
# 1170 questions). Answers here; each finished run is judged per-item (workers 4) by the
# judge loop started below. Resumable. Log: ~/logs/dev30_K30.log (+ _judge.log).
cd ~/M-locomo
source ~/.config/memeval/secrets.env
export TIKTOKEN_CACHE_DIR=$HOME/.cache/tiktoken
RUNS=(full: n1b_drop_tools:gate=drop_tools a_no_synth:synth=false)
COMMON=(--lang zh --pool dev30 --K 30 --model Qwen3-8B --base-url http://localhost:8001/v1 --api-key-env ""
        --extra-body "{\"chat_template_kwargs\":{\"enable_thinking\":false}}" --workers 16
        --no-judge --answer-format reasoning --max-answer-tokens 1024 --method shopmem)
BASE=(embed_base_url=http://localhost:8006/v1 embed_model=Qwen3-Embedding-8B
      rerank_base_url=http://localhost:8007/v1 rerank_model=Qwen3-Reranker-4B)
LOG=~/logs/dev30_K30.log
judge_loop() {
  for spec in "${RUNS[@]}"; do run=dev30_K30_${spec%%:*}
    until grep -q "done -> .*/runs/$run\$" $LOG; do sleep 60; done
    echo "=== judge $run $(date)"
    ~/envs/memeval/bin/python -m memeval.score runs/$run --judge-model gpt-6-luna --base-url ${JUDGE_BASE_URL:-https://api.openai.com/v1} \
        --api-key-env JUDGE_API_KEY --cache .cache/llm.sqlite --workers ${JUDGE_WORKERS:-4} 2>&1 | grep "Judge acc"
  done; echo ALLDONE
}
judge_loop > ~/logs/dev30_K30_judge.log 2>&1 &
for spec in "${RUNS[@]}"; do name=dev30_K30_${spec%%:*}; cfg=${spec#*:}
  echo "=== $name $(date)"
  ~/envs/memeval/bin/python -m memeval.run "${COMMON[@]}" --run-name $name --method-config "${BASE[@]}" $cfg 2>&1 | tail -3
done
echo ALLDONE
wait
