#!/bin/bash
# Reader GRPO on lab (lent GPUs 0,1,2,7; 2026-10-11): one group = a Reader vLLM (runtime LoRA) on one GPU,
# the trainer on another, and a rollout service (memeval.reader_rl serve) on this host, all local (no tunnel).
#   RUN=reader_grpo_lr5e6 VLLM_GPU=0 TRAIN_GPU=1 VPORT=18301 RPORT=18311 LR=5e-6 \
#     bash scripts/run_reader_grpo_lab.sh [extra train_reader_grpo.py args, e.g. --max-steps 40 / --resume]
# Same init / pool / hyper-parameters as reader_grpo1 (autoDL-B, lr 1e-6, now runs/reader_grpo1_B) except --lr.
# luna judge concurrency 4 per group (two groups = 8 in total). Logs ~/logs/$RUN.{vllm,serve,train}.log;
# the train log ends with ALLDONE or FAILED.
set -uo pipefail
cd ~/M-locomo
source ~/.config/memeval/secrets.env
export TIKTOKEN_CACHE_DIR=$HOME/.cache/tiktoken
export PATH=$HOME/envs/vllm/bin:$PATH
L=~/logs/$RUN
M=models/Qwen3-8B
mkdir -p runs/$RUN
if ! curl -s -m 5 localhost:$VPORT/v1/models > /dev/null; then
  VLLM_ALLOW_RUNTIME_LORA_UPDATING=True CUDA_VISIBLE_DEVICES=$VLLM_GPU setsid nohup vllm serve $M --served-model-name Qwen3-8B \
      --port $VPORT --enable-lora --max-lora-rank 16 --max-loras 3 --max-model-len 16384 --max-num-seqs 64 \
      --gpu-memory-utilization 0.90 > $L.vllm.log 2>&1 < /dev/null &
fi
if ! curl -s -m 5 -o /dev/null localhost:$RPORT/rollout; then
  setsid nohup ~/envs/memeval/bin/python -m memeval.reader_rl serve --run-name $RUN \
      --reader-base-url http://localhost:$VPORT/v1 --bank-dir runs/train_K20_banks_req --port $RPORT \
      --workers 32 --gpt-workers 4 > $L.serve.log 2>&1 < /dev/null &
fi
for i in $(seq 120); do curl -s -m 5 localhost:$VPORT/v1/models > /dev/null && break; sleep 10; done
curl -s -m 5 localhost:$VPORT/v1/models > /dev/null || { echo "FAILED vllm did not come up" >> $L.train.log; exit 1; }
for i in $(seq 60); do curl -s -m 5 -o /dev/null localhost:$RPORT/rollout && break; sleep 5; done
cd scripts
CUDA_VISIBLE_DEVICES=$TRAIN_GPU ~/envs/sft/bin/python train_reader_grpo.py --pool-file ../runs/reader_grpo_pool/pool.json \
    --init-adapter ../runs/reader_sft_sol_v3_vote4/adapter --out ../runs/$RUN --model $M --lr $LR \
    --vllm-url http://localhost:$VPORT --rollout-url http://localhost:$RPORT/rollout "$@" >> $L.train.log 2>&1 \
  && echo "ALLDONE $(date)" >> $L.train.log || echo "FAILED $(date)" >> $L.train.log
