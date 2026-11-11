#!/bin/bash
# Serve Qwen3-4B + writer_ep3 on lab GPU 7 :18010 unless something already serves there.
cd ~/M-locomo
pgrep -f "vllm serve models/Qwen3-4B" > /dev/null && { echo "already running"; exit 0; }
bash scripts/serve_vllm.sh Qwen3-4B 7 18010 --max-model-len 16384 --enable-lora --max-loras 1 --max-num-seqs 32 \
    --lora-modules writer_ep3=runs/sft_writer_K20/adapter_ep3
