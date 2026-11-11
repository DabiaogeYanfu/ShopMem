#!/bin/bash
# dev30 eval of lab GRPO checkpoints (stage R2 gate vs sol-SFT Reader 84.5), same setup as
# scripts/run_reader_gate1.sh, on the group own Reader vLLM (shared with its rollouts). Waits for each
# runs/$RUN/adapter_step<k> to appear, loads it as <tag>_step<k>, evaluates, unloads.
#   RUN=reader_grpo_lr5e6 VPORT=18301 STEPS="10 20 30" bash scripts/run_reader_grpo_eval_lab.sh
# STEPS may include "final" (runs/$RUN/adapter, written at the end of the epoch). luna judge workers
# JUDGE_WORKERS (2: with two groups x (rollout judge 4 + eval judge 2) = 12 at most, rollouts mostly idle while
# training; the rule is 8 sustained). Since 2026-10-12 the judge runs under ~/.luna_judge.lock (shared with
# scripts/run_reader_test90.sh) with 4 workers: + the stab1 rollout judge (4) = 8 at most. Log: ~/logs/${RUN}.eval.log (ALLDONE). Results runs/dev30_<tag>_step<k>.
cd ~/M-locomo
source ~/.config/memeval/secrets.env
export TIKTOKEN_CACHE_DIR=$HOME/.cache/tiktoken
NOTHINK="{\"chat_template_kwargs\":{\"enable_thinking\":false}}"
TAG=${RUN#reader_}
for K in $STEPS; do
  if [ "$K" = final ]; then A=runs/$RUN/adapter; N=${TAG}_final; else A=runs/$RUN/adapter_step$K; N=${TAG}_step$K; fi
  OUT=dev30_$N
  until [ -f $A/adapter_model.safetensors ]; do sleep 60; done; sleep 30
  echo "== $N $(date)"
  curl -s -X POST localhost:$VPORT/v1/load_lora_adapter -H "Content-Type: application/json" \
      -d "{\"lora_name\":\"$N\",\"lora_path\":\"$(readlink -f $A)\"}"; echo
  ~/envs/memeval/bin/python -m memeval.run --lang en --pool dev30 --K 30 --model $N --base-url http://localhost:$VPORT/v1 \
      --api-key-env "" --extra-body "$NOTHINK" --workers 16 --no-judge --max-answer-tokens 1024 \
      --run-name $OUT --method shopmem_reader \
      --method-config bank_dir=runs/dev30_banks_req t_max=2 closed_view=rule \
      embed_base_url=http://localhost:8006/v1 embed_model=Qwen3-Embedding-8B \
      rerank_base_url=http://localhost:8007/v1 rerank_model=Qwen3-Reranker-4B 2>&1 | tail -2
  flock ~/.luna_judge.lock ~/envs/memeval/bin/python -m memeval.score runs/$OUT --judge-model gpt-6-luna --base-url ${JUDGE_BASE_URL:-https://api.openai.com/v1} \
      --api-key-env JUDGE_API_KEY --cache .cache/llm.sqlite --workers ${JUDGE_WORKERS:-4} 2>&1 | grep "Judge acc"
  curl -s -X POST localhost:$VPORT/v1/unload_lora_adapter -H "Content-Type: application/json" -d "{\"lora_name\":\"$N\"}"; echo
done
echo ALLDONE
