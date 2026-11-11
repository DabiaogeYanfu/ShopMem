#!/bin/bash
# Reader GRPO on autoDL-B (two GPUs): vLLM serves the policy on GPU 0 (runtime LoRA loading; the trainer
# loads every step adapter by name), scripts/train_reader_grpo.py trains on GPU 1. The rollout service
# runs on lab (memeval.reader_rl serve) and lab keeps the tunnel up (scripts/tunnel_reader_grpo.sh):
# lab :18100 -> B vLLM, B :18120 -> lab rollouts.
#   RUN=reader_grpo1 INIT=runs/reader_sft_sol_v3_vote4/adapter POOL=runs/reader_grpo_pool/pool.json \
#     bash scripts/run_reader_grpo.sh [extra train_reader_grpo.py args]
# Log: /root/autodl-tmp/logs/$RUN.log (ends with ALLDONE or FAILED)
set -uo pipefail
cd /root/autodl-tmp/M-locomo
source /root/autodl-tmp/env.sh
M=/root/autodl-tmp/models/Qwen3-8B
LOG=/root/autodl-tmp/logs/$RUN.log
mkdir -p runs/$RUN
if ! curl -s -m 5 localhost:18100/v1/models > /dev/null; then
  VLLM_ALLOW_RUNTIME_LORA_UPDATING=True CUDA_VISIBLE_DEVICES=0 setsid nohup vllm serve $M --served-model-name Qwen3-8B \
      --port 18100 --enable-lora --max-lora-rank 16 --max-loras 2 --max-model-len 16384 --max-num-seqs 64 \
      --gpu-memory-utilization 0.90 > /root/autodl-tmp/logs/vllm_reader_18100.log 2>&1 < /dev/null &
  for i in $(seq 120); do curl -s -m 5 localhost:18100/v1/models > /dev/null && break; sleep 10; done
fi
curl -s -m 5 localhost:18100/v1/models > /dev/null || { echo "FAILED vllm did not come up" >> $LOG; exit 1; }
curl -s -m 5 -o /dev/null localhost:18120/rollout || { echo "FAILED rollout service not reachable (tunnel?)" >> $LOG; exit 1; }
cd scripts
CUDA_VISIBLE_DEVICES=1 python train_reader_grpo.py --pool-file ../$POOL --init-adapter ../$INIT --out ../runs/$RUN \
    --model $M --vllm-url http://localhost:18100 --rollout-url http://localhost:18120/rollout "$@" >> $LOG 2>&1 \
  && echo "ALLDONE $(date)" >> $LOG || echo "FAILED $(date)" >> $LOG
