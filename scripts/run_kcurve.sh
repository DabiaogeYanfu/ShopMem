#!/bin/bash
# K-curve (accuracy vs history length), test90 EN, 3510 q at every K (K must divide 90; K=30 = main table,
# reused). Data: generation/build_long_history.py --langs en --pools test90 --test90-ks 1 10 90.
# Same configs as the K=30 main table:
#   ShopMem  = writer_ep3 + request_units banks (run_test90_request_units.sh) -> stab1 final Reader t_max 2
#              (run_reader_test90.sh); Writer then Reader served on lab GPU 2 (lent by the user, 2026-10-25).
#   baselines = rag_hybrid_rr (top_k 10, Embedding-8B + Reranker-4B) and full_context_plain
#              (max_context_tokens=100000, as run_test90_fullctx.sh) on the 8B :8001.
# Chain A (GPU 2): Writer K=1,10,90 -> swap to 8B+LoRA -> Reader K=1,10,90 -> free GPU 2.
# Chain B (:8001): baselines. Each finished run is luna-judged under ~/.luna_judge.lock (workers 8).
# Runs with 3510 predictions are not regenerated (resumable).
#   [CHAINS=a|b] bash scripts/run_kcurve.sh      Logs: ~/logs/kcurve_{A,B}.log (ALLDONE each). Results runs/v2test90_K<k>_<name>
cd ~/M-locomo
source ~/.config/memeval/secrets.env
export TIKTOKEN_CACHE_DIR=$HOME/.cache/tiktoken
export PATH=$HOME/envs/vllm/bin:$PATH
KS=${KS:-"1 10 90"}
GPU=2
NOTHINK="{\"chat_template_kwargs\":{\"enable_thinking\":false}}"
E8=(embed_base_url=http://localhost:8006/v1 embed_model=Qwen3-Embedding-8B)
RR=(rerank_base_url=http://localhost:8007/v1 rerank_model=Qwen3-Reranker-4B)
PY=~/envs/memeval/bin/python

done3510() { [ "$(wc -l < runs/$1/predictions.jsonl 2>/dev/null)" = 3510 ]; }
judge() {
  done3510 $1 || { echo "FAILED $1 (no 3510 predictions)"; return; }
  flock ~/.luna_judge.lock $PY -m memeval.score runs/$1 --judge-model gpt-6-luna --base-url ${JUDGE_BASE_URL:-https://api.openai.com/v1} \
      --api-key-env JUDGE_API_KEY --cache .cache/llm.sqlite --workers 8 2>&1 | grep "Judge acc"
}
serve() {  # serve <model> <port> <log> [vllm args]; waits until healthy
  local M=$1 P=$2 L=$3; shift 3
  CUDA_VISIBLE_DEVICES=$GPU setsid nohup vllm serve $HOME/models/$M --served-model-name $M --port $P \
      --gpu-memory-utilization 0.90 "$@" > $L 2>&1 < /dev/null &
  echo $! > ~/logs/kcurve_vllm.pid
  until curl -sf localhost:$P/health > /dev/null; do sleep 15; done; echo "served $M :$P $(date)"
}
unserve() {
  kill $(cat ~/logs/kcurve_vllm.pid) 2>/dev/null
  while nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits -i $GPU | awk '{exit !($1>1000)}'; do sleep 10; done
  echo "GPU $GPU free $(date)"
}

chain_a() {
  local need=0
  for K in $KS; do done3510 v2test90_K${K}_writer_sft_ep3_req || need=1; done
  if [ $need = 1 ]; then
    serve Qwen3-4B 18010 ~/logs/vllm_kcurve_writer_18010.log --max-model-len 16384 --enable-lora --max-loras 1 \
        --max-num-seqs 32 --lora-modules writer_ep3=runs/sft_writer_K20/adapter_ep3
    for K in $KS; do NAME=v2test90_K${K}_writer_sft_ep3_req
      echo "=== $NAME $(date)"
      done3510 $NAME || $PY -m memeval.run --lang en --pool test90 --K $K --model Qwen3-8B \
          --base-url http://localhost:8001/v1 --api-key-env "" --extra-body "$NOTHINK" --workers 16 --no-judge \
          --answer-format reasoning --max-answer-tokens 1024 --method shopmem_writer --run-name $NAME \
          --method-config "${E8[@]}" "${RR[@]}" \
            writer_model=writer_ep3 writer_base_url=http://localhost:18010/v1 writer_api_key_env= write_workers=16 \
            closed_view=rule "writer_extra_body=$NOTHINK" request_units=true bank_path=runs/$NAME/banks 2>&1 | tail -1
      [ -d runs/$NAME/banks ] || echo "FAILED $NAME (no banks)"
    done
    unserve
  fi
  # LoRA mounted at startup (runtime load_lora_adapter 404s without VLLM_ALLOW_RUNTIME_LORA_UPDATING)
  serve Qwen3-8B 18302 ~/logs/vllm_kcurve_reader_18302.log --max-model-len 16384 --enable-lora --max-loras 3 --max-num-seqs 64 \
      --lora-modules grpo_stab1_final=$(readlink -f runs/reader_grpo_stab1/adapter)
  curl -s localhost:18302/v1/models | grep -q '"grpo_stab1_final"' || { echo "FAILED grpo_stab1_final not served"; unserve; echo ALLDONE; return; }
  for K in $KS; do OUT=v2test90_K${K}_grpo_stab1_final
    echo "=== $OUT $(date)"
    [ -d runs/v2test90_K${K}_writer_sft_ep3_req/banks ] || { echo "SKIP $OUT (no banks)"; continue; }
    done3510 $OUT || $PY -m memeval.run --lang en --pool test90 --K $K --model grpo_stab1_final \
        --base-url http://localhost:18302/v1 --api-key-env "" --extra-body "$NOTHINK" --workers 16 --no-judge \
        --max-answer-tokens 1024 --run-name $OUT --method shopmem_reader \
        --method-config bank_dir=runs/v2test90_K${K}_writer_sft_ep3_req/banks t_max=2 closed_view=rule \
          "${E8[@]}" "${RR[@]}" 2>&1 | tail -2
    judge $OUT
  done
  unserve
  echo ALLDONE
}

chain_b() {
  local B=(--lang en --pool test90 --model Qwen3-8B --base-url http://localhost:8001/v1 --api-key-env ""
           --extra-body "$NOTHINK" --workers 16 --no-judge)
  for K in $KS; do
    N=v2test90_K${K}_rag_hybrid_rr; echo "=== $N $(date)"
    done3510 $N || $PY -m memeval.run "${B[@]}" --K $K --run-name $N --method rag_hybrid \
        --answer-format reasoning --max-answer-tokens 1024 --method-config top_k=10 "${E8[@]}" "${RR[@]}" 2>&1 | tail -2
    judge $N
    N=v2test90_K${K}_full_context_plain; echo "=== $N $(date)"
    done3510 $N || $PY -m memeval.run "${B[@]}" --K $K --run-name $N --method full_context \
        --method-config max_context_tokens=100000 2>&1 | tail -2
    judge $N
  done
  echo ALLDONE
}

CHAINS=${CHAINS:-"a b"}  # e.g. CHAINS=a to rerun only chain A while B is still going
[[ $CHAINS == *a* ]] && { chain_a > ~/logs/kcurve_A.log 2>&1 & }
[[ $CHAINS == *b* ]] && { chain_b > ~/logs/kcurve_B.log 2>&1 & }
wait
