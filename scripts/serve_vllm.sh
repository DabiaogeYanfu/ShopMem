#!/bin/bash
# Serve a local model with vLLM's OpenAI-compatible API on the lab server.
#
#   scripts/serve_vllm.sh <model-dir-name> <gpu-ids> <port> [extra vllm args...]
#   scripts/serve_vllm.sh Qwen3-8B 3,4 8001 --max-model-len 131072 \
#       --rope-scaling '{"rope_type":"yarn","factor":4.0,"original_max_position_embeddings":32768}'
#   scripts/serve_vllm.sh Qwen3-Embedding-0.6B 5 8002 --runner pooling
#
# GPUs 0-2 belong to other users' services; use 3-7 only. Tensor parallel size is the
# number of GPUs given. Logs go to ~/logs/vllm_<model>_<port>.log; the served model name
# is the directory name, so clients use e.g. --model Qwen3-8B.
set -euo pipefail
MODEL=$1; GPUS=$2; PORT=$3; shift 3
case ",$GPUS," in *,0,*|*,1,*|*,2,*) echo "GPUs 0-2 are reserved for other services" >&2; exit 1;; esac
TP=$(echo "$GPUS" | tr ',' '\n' | wc -l)
LOG=$HOME/logs/vllm_${MODEL}_${PORT}.log
# the venv's bin/ must be on PATH: vLLM compiles kernels with the ninja installed there
export PATH=$HOME/envs/vllm/bin:$PATH
CUDA_VISIBLE_DEVICES=$GPUS setsid nohup vllm serve "$HOME/models/$MODEL" \
    --served-model-name "$MODEL" --port "$PORT" --tensor-parallel-size "$TP" \
    --gpu-memory-utilization 0.90 "$@" > "$LOG" 2>&1 < /dev/null &
echo "started $MODEL on GPUs $GPUS (tp=$TP) port $PORT, pid $!, log $LOG"
