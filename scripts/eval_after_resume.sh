#!/bin/bash
# dev30 evals of the resumed part (step 50, 60 and the final adapter), after the first eval pass (10-40).
# runs/$RUN/adapter is also written when the first run stops at --max-steps 40, so "final" is evaluated only
# once the resumed run has ended (a 2nd ALLDONE in the train log); a step the epoch does not reach is skipped.
cd ~/M-locomo
T=~/logs/$RUN.train.log
done2() { [ "$(grep -c -E "^(ALLDONE|FAILED)" $T)" -ge 2 ]; }
until grep -q ALLDONE ~/logs/$RUN.eval.log; do sleep 120; done
for K in 50 60; do
  until [ -f runs/$RUN/adapter_step$K/adapter_model.safetensors ] || done2; do sleep 120; done
  [ -f runs/$RUN/adapter_step$K/adapter_model.safetensors ] && STEPS=$K bash scripts/run_reader_grpo_eval_lab.sh
done
until done2; do sleep 120; done
grep -q "^FAILED" <(grep -E "^(ALLDONE|FAILED)" $T | tail -1) && { echo "resumed run FAILED, no final eval"; exit 1; }
STEPS=final bash scripts/run_reader_grpo_eval_lab.sh
