#!/bin/bash
# sol rescue pilot: 99 no_kept questions x 4 gpt-6.1-sol trajectories, luna judge (resumable; retries on crash)
cd ~/M-locomo
set -a; source ~/.config/memeval/secrets.env; set +a
for i in 1 2 3 4 5 6; do
  ~/envs/memeval/bin/python -m memeval.reader_cold generate --run-name sol_pilot_K20 \
    --bank-dir runs/topr1_train_K20/banks --pool train --K 20 --n 4 --n-search 2 \
    --teacher-model gpt-6.1-sol --question-ids runs/sol_pilot_K20/question_ids.txt \
    --workers 16 --gpt-workers 8 && break
  echo "RETRY $i"; sleep 30
done
~/envs/memeval/bin/python scripts/sol_pilot.py summary
echo ALLDONE
