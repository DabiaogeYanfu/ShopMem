#!/bin/bash
# Reader GRPO training-curve figure: stabilised GRPO (stab1, the method) vs standard GRPO (lr1e-5, diverged).
cd ~/M-locomo
~/envs/memeval/bin/python -m memeval.figures grpo \
    --runs runs/reader_grpo_stab1 runs/reader_grpo_lr1e5 \
    --sft-run runs/dev30_gate1_reader_sft_sol \
    --label "reader_grpo_stab1=Stabilised GRPO (ours)" --label "reader_grpo_lr1e5=Standard GRPO" \
    --out results/reader_grpo/grpo_curve
