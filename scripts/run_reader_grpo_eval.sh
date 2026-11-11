#!/bin/bash
# dev30 eval of reader_grpo1 checkpoints (stage R2 gate: vs sol-SFT Reader 84.5), same setup as
# scripts/run_reader_gate1.sh. Each B adapter runs/reader_grpo1/adapter_step<k> is loaded into the GRPO
# vLLM (B GPU0 :18100, shared with rollouts) as grpo1_step<k>, evaluated, then unloaded.
#   STEPS="10" bash scripts/run_reader_grpo_eval.sh      Log: ~/logs/reader_grpo_eval.log (ALLDONE)
cd ~/M-locomo
source ~/.config/memeval/secrets.env
export TIKTOKEN_CACHE_DIR=$HOME/.cache/tiktoken
NOTHINK="{\"chat_template_kwargs\":{\"enable_thinking\":false}}"
for K in $STEPS; do
  N=grpo1_step$K; RUN=dev30_$N
  echo "== $N $(date)"
  ssh autoDL-B "bash /root/autodl-tmp/M-locomo/scripts/vllm_lora.sh load $N /root/autodl-tmp/M-locomo/runs/reader_grpo1/adapter_step$K"
  ~/envs/memeval/bin/python -m memeval.run --lang en --pool dev30 --K 30 --model $N --base-url http://localhost:18100/v1 \
      --api-key-env "" --extra-body "$NOTHINK" --workers 16 --no-judge --max-answer-tokens 1024 \
      --run-name $RUN --method shopmem_reader \
      --method-config bank_dir=runs/dev30_banks_req t_max=2 closed_view=rule \
      embed_base_url=http://localhost:8006/v1 embed_model=Qwen3-Embedding-8B \
      rerank_base_url=http://localhost:8007/v1 rerank_model=Qwen3-Reranker-4B 2>&1 | tail -2
  ~/envs/memeval/bin/python -m memeval.score runs/$RUN --judge-model gpt-6-luna --base-url ${JUDGE_BASE_URL:-https://api.openai.com/v1} \
      --api-key-env JUDGE_API_KEY --cache .cache/llm.sqlite --workers 8 2>&1 | grep "Judge acc"
  ssh autoDL-B "bash /root/autodl-tmp/M-locomo/scripts/vllm_lora.sh unload $N"
done
echo ALLDONE
