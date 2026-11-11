#!/bin/bash
# After the micu generation and its judge loops finish: move the 255 suspect pairs out of the micu run,
# re-generate them on yan-gpt (sol, luna judge inline), then judge them with sol on micu-gpt.
cd ~/M-locomo
set -a; source ~/.config/memeval/secrets.env; set +a
PY=~/envs/memeval/bin/python
echo "[$(date)] waiting for micu generation + judge loops"
while pgrep -f "run_reader_cold_sol_micu.sh|judge_reader_cold_sol.sh reader_cold_sol_K20_micu" > /dev/null; do sleep 60; done
echo "[$(date)] micu jobs finished"
$PY -m scripts.quarantine_replaced_by_yan || { echo "QUARANTINE FAILED"; exit 1; }
scripts/run_reader_cold_sol_yanredo.sh
n=$(wc -l < runs/reader_cold_sol_K20_yanredo/trajectories.jsonl); echo "[$(date)] yanredo rows: $n (expected 255)"
# sol judge of the redo on micu-gpt: paused with the other micu sol judging (quota); run later
echo "[$(date)] CHAIN_DONE"
