#!/bin/bash
# Did SFT teach the Writer? (v2 phase 1, step 8.) ShopMem-Writer on the dev30 pool (K=30, 1 history,
# 1170 questions) with three writers and the same frozen 8B answerer: the SFT adapter (Qwen3-4B +
# adapter_ep3, vLLM on autoDL-B reached through the lab tunnel localhost:18010), the luna teacher,
# and 8B zero-shot. Answers here; each finished run is judged per-item by luna (GPT concurrency 8).
# Resumable. Log: ~/logs/dev30_writer.log (+ _judge.log).
cd ~/M-locomo
source ~/.config/memeval/secrets.env
export TIKTOKEN_CACHE_DIR=$HOME/.cache/tiktoken
NOTHINK='{"chat_template_kwargs":{"enable_thinking":false}}'
ALL=(
  "sft_ep3|writer_model=writer_ep3 writer_base_url=http://localhost:18010/v1 writer_api_key_env= write_workers=16"
  "topr1|writer_model=writer_topr1 writer_base_url=http://localhost:18010/v1 writer_api_key_env= write_workers=16"
  "topr1b_step200|writer_model=topr1b_step200 writer_base_url=http://localhost:18010/v1 writer_api_key_env= write_workers=16"
  "topr1b_final|writer_model=topr1b_final writer_base_url=http://localhost:18010/v1 writer_api_key_env= write_workers=16"
  "topr1c_step100|writer_model=topr1c_step100 writer_base_url=http://localhost:18010/v1 writer_api_key_env= write_workers=16"
  "topr1c_step200|writer_model=topr1c_step200 writer_base_url=http://localhost:18010/v1 writer_api_key_env= write_workers=16"
  "topr1c_step300|writer_model=topr1c_step300 writer_base_url=http://localhost:18010/v1 writer_api_key_env= write_workers=16"
  "topr1c_final|writer_model=topr1c_final writer_base_url=http://localhost:18010/v1 writer_api_key_env= write_workers=16"
  "8b_zs|"
  "luna|writer_model=gpt-6-luna writer_base_url=${JUDGE_BASE_URL:-https://api.openai.com/v1} writer_api_key_env=JUDGE_API_KEY write_workers=8"
)
# topr1 = the TOPR round-1 adapter, topr1b_* = the shift-0.02 redo checkpoints (same B endpoint). ONLY="sft_ep3 luna" runs a subset; TAG=_cv MCFG="closed_view=all" adds read-stage settings (writes replay
# from the LLM cache), e.g. ONLY="sft_ep3 luna" TAG=_cv MCFG=closed_view=all scripts/run_dev30_writer.sh
RUNS=()
for spec in "${ALL[@]}"; do
  [ -z "$ONLY" ] || [[ " $ONLY " == *" ${spec%%|*} "* ]] || continue
  RUNS+=("${spec%%|*}${TAG}|${spec#*|} ${MCFG}")
done
LOG=~/logs/dev30_writer${TAG}.log
COMMON=(--lang en --pool dev30 --K 30 --model Qwen3-8B --base-url http://localhost:8001/v1 --api-key-env ""
        --extra-body "$NOTHINK" --workers 16 --no-judge --answer-format reasoning --max-answer-tokens 1024
        --method shopmem_writer)
BASE=(embed_base_url=http://localhost:8006/v1 embed_model=Qwen3-Embedding-8B
      rerank_base_url=http://localhost:8007/v1 rerank_model=Qwen3-Reranker-4B)
judge_loop() {
  for spec in "${RUNS[@]}"; do run=dev30_writer_${spec%%|*}
    until grep -q "done -> .*/runs/$run\$" $LOG; do sleep 60; done
    echo "=== judge $run $(date)"
    ~/envs/memeval/bin/python -m memeval.score runs/$run --judge-model gpt-6-luna --base-url ${JUDGE_BASE_URL:-https://api.openai.com/v1} \
        --api-key-env JUDGE_API_KEY --cache .cache/llm.sqlite --workers ${JUDGE_WORKERS:-8} 2>&1 | grep "Judge acc"
  done; echo ALLDONE
}
judge_loop > ~/logs/dev30_writer${TAG}_judge.log 2>&1 &
for spec in "${RUNS[@]}"; do name=dev30_writer_${spec%%|*}; cfg=${spec#*|}
  [[ "$cfg" == *localhost:18010* ]] && cfg="$cfg writer_extra_body=$NOTHINK"
  echo "=== $name $(date)"
  ~/envs/memeval/bin/python -m memeval.run "${COMMON[@]}" --run-name $name \
      --method-config "${BASE[@]}" trace_path=runs/$name/trace.jsonl $cfg 2>&1 | tail -3
done
echo ALLDONE
wait
