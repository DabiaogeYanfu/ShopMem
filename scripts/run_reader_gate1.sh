#!/bin/bash
# Reader gate 1 (实验计划_v3 R1): SFT Readers on dev30 over the SFT-Writer banks with request units
# (runs/dev30_banks_req), greedy, t_max 2; Reader served by vLLM on autoDL-B GPU0 (lab :18100 via
# scripts/tunnel_reader_grpo.sh). Baseline: one-shot pipeline dev30_writer_sft_ep3_rule_req (54.6).
#   READERS="reader_sft_sol reader_sft_luna" bash scripts/run_reader_gate1.sh
# Log: ~/logs/reader_gate1.log (ALLDONE)
cd ~/M-locomo
source ~/.config/memeval/secrets.env
export TIKTOKEN_CACHE_DIR=$HOME/.cache/tiktoken
NOTHINK="{\"chat_template_kwargs\":{\"enable_thinking\":false}}"
for R in ${READERS:-reader_sft_sol reader_sft_luna}; do
  RUN=dev30_gate1_$R
  echo "== $R $(date)"
  ~/envs/memeval/bin/python -m memeval.run --lang en --pool dev30 --K 30 --model $R --base-url http://localhost:18100/v1 \
      --api-key-env "" --extra-body "$NOTHINK" --workers 16 --no-judge --max-answer-tokens 1024 \
      --run-name $RUN --method shopmem_reader \
      --method-config bank_dir=runs/dev30_banks_req t_max=2 closed_view=rule \
      embed_base_url=http://localhost:8006/v1 embed_model=Qwen3-Embedding-8B \
      rerank_base_url=http://localhost:8007/v1 rerank_model=Qwen3-Reranker-4B 2>&1 | tail -3
  ~/envs/memeval/bin/python -m memeval.score runs/$RUN --judge-model gpt-6-luna --base-url ${JUDGE_BASE_URL:-https://api.openai.com/v1} \
      --api-key-env JUDGE_API_KEY --cache .cache/llm.sqlite --workers 8 2>&1 | grep "Judge acc"
done
echo ALLDONE
