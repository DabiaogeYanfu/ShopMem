#!/bin/bash
# After the 5-vote luna majority finishes (ALLDONE in ~/logs/judge_luna5.log): build two v3 candidates, both with
# constraint_recall lapse claims dropped -- v3_vote3 (>= 3/5 CORRECT votes) and v3_vote4 (>= 4/5) -- and diff each
# against v2 and against each other. The user picks the SFT version from the counts.
# Prints CHAIN_V3_DONE on success, CHAIN_V3_FAILED (and stops) on the first failing step.
set -e
trap 'echo CHAIN_V3_FAILED' ERR
cd ~/M-locomo
until grep -q ALLDONE ~/logs/judge_luna5.log; do sleep 600; done
PY=~/envs/memeval/bin/python
for k in 3 4; do
  $PY -m memeval.reader_cold build --run-name reader_cold_sol_K20_all_v3_vote$k \
      --runs reader_cold_sol_K20 reader_cold_sol_K20_micu reader_cold_sol_K20_yanredo \
      --votes results/reader_cold_sol_luna5.jsonl --min-votes $k --drop-lapse-claims
  $PY -m scripts.diff_build runs/reader_cold_sol_K20_all_v2 runs/reader_cold_sol_K20_all_v3_vote$k
done
$PY -m scripts.diff_build runs/reader_cold_sol_K20_all_v3_vote3 runs/reader_cold_sol_K20_all_v3_vote4
echo CHAIN_V3_DONE
