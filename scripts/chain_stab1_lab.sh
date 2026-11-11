#!/bin/bash
# lr1e-5 → stab1 swap (user approved 2026-10-12 ~13:40 CST): lr1e-5 diverging since step ~24 (same-question acc vs
# SFT pool +9 pts at steps 11-23 → -15 at step 30; entropy 1.76, KL 0.17). Wait for adapter_step30, stop the whole
# lr1e5 group (its eval waiter / resume chain first, then trainer, launcher, rollout service, vLLM :18302 on GPU2),
# start reader_grpo_stab1 on GPU2 (vLLM :18302) / GPU7 (trainer), rollout service :18312, then evaluate lr1e5
# step30 and later the stab1 checkpoints on the idle GPU0 vLLM :18301 (not the group's own vLLM).
# Log: ~/logs/chain_stab1_lab.log
cd ~/M-locomo
R=runs/reader_grpo_lr1e5
for p in 4179556 4179557 4134388; do kill $p 2>/dev/null && echo "killed lr1e5 waiter/resume chain $p"; done
until [ -f $R/adapter_step30/adapter_model.safetensors ]; do sleep 30; done
sleep 120  # state.pt is written right after the adapter
echo "lr1e5 adapter_step30 saved $(date)"
for p in 4036940 4030215 4030227 4030222; do kill $p 2>/dev/null && echo "kill $p"; done
for i in $(seq 60); do
  m2=$(nvidia-smi -i 2 --query-gpu=memory.used --format=csv,noheader,nounits)
  m7=$(nvidia-smi -i 7 --query-gpu=memory.used --format=csv,noheader,nounits)
  [ "$m2" -lt 1000 ] && [ "$m7" -lt 1000 ] && ! curl -s -m 3 -o /dev/null localhost:18312/rollout && break
  sleep 10
done
echo "GPU2 ${m2} MiB, GPU7 ${m7} MiB $(date)"
{ [ "$m2" -lt 1000 ] && [ "$m7" -lt 1000 ]; } || { echo "GPUs not free, NOT starting stab1"; exit 1; }
echo "stopped after step30 (diverged since ~step24; same-q acc vs SFT pool -15 at step30), user 2026-10-12 $(date)" > $R/ABORTED.txt
RUN=reader_grpo_stab1 VLLM_GPU=2 TRAIN_GPU=7 VPORT=18302 RPORT=18312 LR=1e-5 setsid nohup bash scripts/run_reader_grpo_lab.sh \
    --beta 0.02 --adv-norm none --G-sample 32 --select-weight --tis-cap 2 < /dev/null > /dev/null 2>&1 &
echo "stab1 launched $(date)"
RUN=reader_grpo_lr1e5 VPORT=18301 STEPS=30 bash scripts/run_reader_grpo_eval_lab.sh >> ~/logs/reader_grpo_lr1e5.eval.log 2>&1
echo "lr1e5 step30 eval: $(grep 'Judge acc' ~/logs/reader_grpo_lr1e5.eval.log | tail -1) $(date)"
RUN=reader_grpo_stab1 VPORT=18301 STEPS="10 20 30 40 50 60 70 final" bash scripts/run_reader_grpo_eval_lab.sh >> ~/logs/reader_grpo_stab1.eval.log 2>&1
echo "stab1 evals ALLDONE $(date)"
