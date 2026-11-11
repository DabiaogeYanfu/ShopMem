#!/bin/bash
# v2 §3.4 ablation: retrain the SFT Writer on an ablated SFT set (memeval.sft_data --ablate <name>),
# hyper-parameters identical to runs/sft_writer_K20 (5 epochs scheduled, adapter_ep3 is the one
# evaluated, as for the main SFT Writer).  scripts/train_sft_ablation.sh <no_rind|no_revid> <gpu>
cd ~/M-locomo
A=$1; GPU=$2; OUT=runs/sft_writer_K20_$A
mkdir -p $OUT
CUDA_VISIBLE_DEVICES=$GPU ~/envs/sft/bin/python scripts/train_writer_sft.py --data runs/teacher_train_K20/sft_$A \
    --out $OUT --model models/Qwen3-4B --max-len 6144 --epochs 5 --lr 1e-4 --accum 8 --warmup 0.05 \
    --lora-r 16 --lora-alpha 32 --lora-dropout 0.05 --eval-every 10 --gen-check 8 --seed 0 > $OUT/train.log 2>&1
echo "ALLDONE $(date)" >> $OUT/train.log
