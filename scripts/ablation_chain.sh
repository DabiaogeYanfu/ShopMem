#!/bin/bash
# Tonight's ablation chain (all GPUs lent to us for the night, 2026-10-08):
#   GPU 2: train no_rind (running) -> train no_revid
#   GPU 7: Writer vLLM :18010; after add_noop's answers, reload with the no_rind LoRA -> test90 eval;
#          after no_revid is trained, reload with the no_revid LoRA -> test90 eval.
# Any failure (training without adapter_ep3, vLLM not up in 15 min) is logged and that variant skipped.
# Log: ~/logs/ablation_chain.log
cd ~/M-locomo
serve() {  # one LoRA at a time on GPU 7; returns 1 if the server is not up within 15 min
  pkill -f "vllm serve models/Qwen3-4B"; sleep 30
  bash scripts/serve_vllm.sh Qwen3-4B 7 18010 --max-model-len 16384 --enable-lora --max-loras 1 --max-num-seqs 32 \
      --lora-modules "$1=$2"
  for i in $(seq 60); do curl -s -m 5 localhost:18010/v1/models | grep -q "\"$1\"" && { echo "served $1 $(date)"; return 0; }; sleep 15; done
  echo "FAILED to serve $1 $(date)"; return 1
}
wait_train() {  # $1 = variant; returns 1 if training ended without adapter_ep3
  until grep -q ALLDONE runs/sft_writer_K20_$1/train.log 2>/dev/null; do sleep 60; done
  [ -f runs/sft_writer_K20_$1/adapter_ep3/adapter_config.json ] && { echo "$1 trained $(date)"; return 0; }
  echo "FAILED: $1 training has no adapter_ep3 $(date)"; return 1
}
eval_variant() {  # $1 = variant
  serve writer_$1 runs/sft_writer_K20_$1/adapter_ep3 || return
  scripts/run_test90_ablation.sh $1 writer_$1 > ~/logs/test90_abl_$1.log 2>&1
  echo "$1 eval finished: $(grep -E 'Judge acc|skip judge' ~/logs/test90_abl_$1.log) $(date)"
}
ok_rind=0; wait_train no_rind && ok_rind=1
setsid nohup scripts/train_sft_ablation.sh no_revid 2 > /dev/null 2>&1 < /dev/null &
until ! pgrep -f "run-name v2test90_K30_writer_abl_add_noop" > /dev/null; do sleep 60; done  # GPU 7 free again
[ $ok_rind = 1 ] && eval_variant no_rind
wait_train no_revid && eval_variant no_revid
echo "ALLDONE $(date)"
