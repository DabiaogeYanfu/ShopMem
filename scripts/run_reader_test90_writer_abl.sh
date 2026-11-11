#!/bin/bash
# Writer ablations re-evaluated with the trained Reader (v3 full method = Writer + Reader): the test90 banks
# of the three ablated Writers (runs/v2test90_K30_writer_abl_{add_noop,no_rind,no_revid}_req/banks, written
# for the one-shot ablation rows 52.6 / 52.8 / 54.5) read by GRPO Reader $READER (default stab1 step30), same
# setup as scripts/run_reader_test90.sh (t_max 2, closed_view rule, eval vLLM GPU2 :18302, luna 4 workers under
# ~/.luna_judge.lock). Compare with v2test90_K30_<reader tag> on the main SFT-Writer banks.
# Waits for $WAIT_PID (the running test90 job on the same vLLM) to exit first.
#   [WAIT_PID=<pid>] [ABLS="no_rind no_revid"] bash scripts/run_reader_test90_writer_abl.sh
# Log: ~/logs/reader_test90_writer_abl.log (ALLDONE). Results runs/v2test90_K30_<abl>_<reader tag>
cd ~/M-locomo
source ~/.config/memeval/secrets.env
export TIKTOKEN_CACHE_DIR=$HOME/.cache/tiktoken
NOTHINK="{\"chat_template_kwargs\":{\"enable_thinking\":false}}"
VPORT=${VPORT:-18302}
N=${READER:-grpo_stab1_step30}
A=${ADAPTER:-runs/reader_grpo_stab1/adapter_step30}
# WAIT_PID (optional): one pid to wait for; /proc is checked, since kill -0 fails on other users' pids
if [ -n "$WAIT_PID" ]; then
  [[ "$WAIT_PID" =~ ^[0-9]+$ ]] || { echo "WAIT_PID must be one pid, got '$WAIT_PID'"; exit 1; }
  while [ -d /proc/$WAIT_PID ]; do sleep 60; done
fi
ABLS=${ABLS:-"add_noop no_rind no_revid"}
echo "start $(date)"
curl -s -X POST localhost:$VPORT/v1/load_lora_adapter -H "Content-Type: application/json" \
    -d "{\"lora_name\":\"$N\",\"lora_path\":\"$(readlink -f $A)\"}"; echo
for abl in $ABLS; do
  OUT=v2test90_K30_writer_abl_${abl}_$N
  echo "== $OUT $(date)"
  [ "$(wc -l < runs/$OUT/predictions.jsonl 2>/dev/null)" = 3510 ] && echo "predictions exist, scoring only" || \
  ~/envs/memeval/bin/python -m memeval.run --lang en --pool test90 --K 30 --model $N --base-url http://localhost:$VPORT/v1 \
      --api-key-env "" --extra-body "$NOTHINK" --workers 16 --no-judge --max-answer-tokens 1024 \
      --run-name $OUT --method shopmem_reader \
      --method-config bank_dir=runs/v2test90_K30_writer_abl_${abl}_req/banks t_max=2 closed_view=rule \
      embed_base_url=http://localhost:8006/v1 embed_model=Qwen3-Embedding-8B \
      rerank_base_url=http://localhost:8007/v1 rerank_model=Qwen3-Reranker-4B 2>&1 | tail -2
  [ -f runs/$OUT/predictions.jsonl ] || { echo "FAILED $OUT"; continue; }
  flock ~/.luna_judge.lock ~/envs/memeval/bin/python -m memeval.score runs/$OUT --judge-model gpt-6-luna \
      --base-url ${JUDGE_BASE_URL:-https://api.openai.com/v1} --api-key-env JUDGE_API_KEY --cache .cache/llm.sqlite --workers 4 2>&1 | grep "Judge acc"
done
curl -s -X POST localhost:$VPORT/v1/unload_lora_adapter -H "Content-Type: application/json" -d "{\"lora_name\":\"$N\"}"; echo
echo ALLDONE
