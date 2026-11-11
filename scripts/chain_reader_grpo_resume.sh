#!/bin/bash
# After a lab GRPO group stops at --max-steps 40 (train log gets its first ALLDONE), resume it from
# state.pt to the end of the epoch (no --max-steps), and evaluate the later checkpoints on dev30.
#   RUN=reader_grpo_lr5e6 VLLM_GPU=0 TRAIN_GPU=1 VPORT=18301 RPORT=18311 LR=5e-6 bash scripts/chain_reader_grpo_resume.sh
# Log: ~/logs/$RUN.chain.log
cd ~/M-locomo
L=~/logs/$RUN
until grep -q -E "^(ALLDONE|FAILED)" $L.train.log; do sleep 120; done
if grep -q "^FAILED" $L.train.log; then echo "train FAILED before step 40, not resuming $(date)"; exit 1; fi
echo "resuming $(date)"
cp runs/$RUN/log.jsonl runs/$RUN/log_until_step40.jsonl
bash scripts/run_reader_grpo_lab.sh --resume
