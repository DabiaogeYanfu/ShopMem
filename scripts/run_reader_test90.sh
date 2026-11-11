#!/bin/bash
# Reader runs on test90 (paper main table, same split as the one-shot pipeline v2test90_K30_writer_sft_ep3_req
# 53.3): sol-SFT Reader (t_max 2), its no-search ablation (t_max 0), and GRPO lr1e-5 step20 (best dev30 ckpt,
# 88.9). Same setup as the dev30 runs (scripts/run_reader_gate1.sh / run_reader_tmax0.sh /
# run_reader_grpo_eval_lab.sh) except --pool test90 and bank_dir = that run's test90 SFT-Writer banks.
# Reader served by the eval vLLM on lab GPU2 :18302 (shared with the stab1 dev30 eval waiter: runtime LoRAs,
# different names). luna judge workers 4 under ~/.luna_judge.lock (shared with the stab1 dev30 eval judge), so
# with the stab1 rollout judge (4) at most 8 concurrent. Runs whose 3510 predictions exist are not regenerated.
#   bash scripts/run_reader_test90.sh      Log: ~/logs/reader_test90.log (ALLDONE). Results runs/v2test90_K30_<name>
cd ~/M-locomo
source ~/.config/memeval/secrets.env
export TIKTOKEN_CACHE_DIR=$HOME/.cache/tiktoken
NOTHINK="{\"chat_template_kwargs\":{\"enable_thinking\":false}}"
VPORT=${VPORT:-18302}
# SPECS (optional) overrides the list: space-separated "lora_name|adapter_dir|t_max|run_suffix" entries, e.g.
#   SPECS="grpo_stab1_step30|runs/reader_grpo_stab1/adapter_step30|2|grpo_stab1_step30" bash scripts/run_reader_test90.sh
SPECS=${SPECS:-"reader_sft_sol|runs/reader_sft_sol_v3_vote4/adapter|2|reader_sft_sol reader_sft_sol|runs/reader_sft_sol_v3_vote4/adapter|0|tmax0_reader_sft_sol grpo_lr1e5_step20|runs/reader_grpo_lr1e5/adapter_step20|2|grpo_lr1e5_step20"}
for spec in $SPECS; do
  IFS="|" read -r N A T name <<< "$spec"
  OUT=v2test90_K30_$name
  echo "== $OUT (t_max $T) $(date)"
  curl -s -X POST localhost:$VPORT/v1/load_lora_adapter -H "Content-Type: application/json" \
      -d "{\"lora_name\":\"$N\",\"lora_path\":\"$(readlink -f $A)\"}"; echo
  [ "$(wc -l < runs/$OUT/predictions.jsonl 2>/dev/null)" = 3510 ] && echo "predictions exist, scoring only" || \
  ~/envs/memeval/bin/python -m memeval.run --lang en --pool test90 --K 30 --model $N --base-url http://localhost:$VPORT/v1 \
      --api-key-env "" --extra-body "$NOTHINK" --workers 16 --no-judge --max-answer-tokens 1024 \
      --run-name $OUT --method shopmem_reader \
      --method-config bank_dir=runs/v2test90_K30_writer_sft_ep3_req/banks t_max=$T closed_view=rule \
      embed_base_url=http://localhost:8006/v1 embed_model=Qwen3-Embedding-8B \
      rerank_base_url=http://localhost:8007/v1 rerank_model=Qwen3-Reranker-4B 2>&1 | tail -2
  [ -f runs/$OUT/predictions.jsonl ] || { echo "FAILED $OUT"; continue; }
  flock ~/.luna_judge.lock ~/envs/memeval/bin/python -m memeval.score runs/$OUT --judge-model gpt-6-luna \
      --base-url ${JUDGE_BASE_URL:-https://api.openai.com/v1} --api-key-env JUDGE_API_KEY --cache .cache/llm.sqlite --workers 4 2>&1 | grep "Judge acc"
  # the t_max 0 run reuses the same adapter name, so unload only after it
  [ "$name" = reader_sft_sol ] || { curl -s -X POST localhost:$VPORT/v1/unload_lora_adapter \
      -H "Content-Type: application/json" -d "{\"lora_name\":\"$N\"}"; echo; }
done
echo ALLDONE
