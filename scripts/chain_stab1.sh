#!/bin/bash
# lr3e-5 → stable GRPO swap (user approved 2026-10-12): wait for the dev30 eval of reader_grpo_lr3e5 step20
# (or for that run to fail before it), stop the whole lr3e5 group (waiters, launcher, trainer, rollout service,
# GPU0 vLLM :18301), then start reader_grpo_stab1 on GPU0 (vLLM) / GPU1 (trainer): same as reader_grpo_lr1e5
# (SFT init, lr 1e-5) but β 0.02, --adv-norm none (Dr. GRPO), --G-sample 32 (train the 8 most extreme) with
# --select-weight (selected Â rescaled to the group's positive / negative mass), --tis-cap 2, no step cap (one epoch), plus its
# dev30 eval waiter. Log: ~/logs/chain_stab1.log
cd ~/M-locomo
E=~/logs/reader_grpo_lr3e5.eval.log
until grep -q "adapter 'grpo_lr3e5_step20' removed" $E \
    || { grep -q -E "^FAILED" ~/logs/reader_grpo_lr3e5.train.log && [ ! -d runs/reader_grpo_lr3e5/adapter_step20 ]; }; do
  sleep 60
done
echo "lr3e5 step20 eval done: $(grep 'Judge acc' $E | tail -1) $(date)"
# processes of the lr3e5 group carry RUN=reader_grpo_lr3e5 in their environment (the GPU0 vLLM does not: it was
# started by the old lr5e6 group, so it is found by its port)
of_run() { for p in $(pgrep -u yf -f "$1"); do tr '\0' '\n' < /proc/$p/environ 2>/dev/null | grep -qx "RUN=reader_grpo_lr3e5" && echo $p; done; }
for pat in "chain_reader_grpo_resume.sh|eval_after_resume.sh|run_reader_grpo_eval_lab.sh" "run_reader_grpo_lab.sh" \
           "train_reader_grpo.py" "reader_rl serve --run-name reader_grpo_lr3e5"; do
  P=$(of_run "$pat"); [ -n "$P" ] && { echo "kill [$pat]: $P"; kill $P; }
done
sleep 5
P=$(pgrep -u yf -f "reader_rl serve --run-name reader_grpo_lr3e5"); [ -n "$P" ] && { echo "kill serve (2nd try) $P"; kill $P; }
V=$(pgrep -u yf -f "vllm serve .* --port 18301 "); [ -n "$V" ] && { echo "kill vllm :18301 $V"; kill $V; }
for i in $(seq 60); do
  m0=$(nvidia-smi -i 0 --query-gpu=memory.used --format=csv,noheader,nounits)
  m1=$(nvidia-smi -i 1 --query-gpu=memory.used --format=csv,noheader,nounits)
  [ "$m0" -lt 1000 ] && [ "$m1" -lt 1000 ] && break
  sleep 10
done
echo "GPU0 ${m0} MiB, GPU1 ${m1} MiB $(date)"
{ [ "$m0" -lt 1000 ] && [ "$m1" -lt 1000 ]; } || { echo "GPUs not free, NOT starting stab1"; exit 1; }
echo "stopped after step20 eval (diverged), last logged $(tail -1 runs/reader_grpo_lr3e5/log.jsonl | grep -o '"step": [0-9]*') $(date)" \
  > runs/reader_grpo_lr3e5/ABORTED.txt
export RUN=reader_grpo_stab1 VLLM_GPU=0 TRAIN_GPU=1 VPORT=18301 RPORT=18311 LR=1e-5
setsid nohup bash scripts/run_reader_grpo_lab.sh --beta 0.02 --adv-norm none --G-sample 32 --select-weight --tis-cap 2 < /dev/null > /dev/null 2>&1 &
STEPS="10 20 30 40 50 60 final" setsid nohup bash scripts/run_reader_grpo_eval_lab.sh < /dev/null >> ~/logs/$RUN.eval.log 2>&1 &
echo "stab1 launched $(date)"
