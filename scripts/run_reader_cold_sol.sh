#!/bin/bash
# Reader cold start with gpt-6.1-sol as teacher (all train K20, 3120 q x 4, 2 hinted), format hint on,
# luna judge; resumable, retries on crash.
cd ~/M-locomo
set -a; source ~/.config/memeval/secrets.env; set +a
for i in $(seq 1 10); do
  ~/envs/memeval/bin/python -m memeval.reader_cold generate --run-name reader_cold_sol_K20 \
    --bank-dir runs/topr1_train_K20/banks --pool train --K 20 --n 4 --n-search 2 \
    --teacher-model gpt-6.1-sol --format-hint --format-retries 2 \
    --workers 16 --gpt-workers 8 && break
  echo "RETRY $i"; sleep 60
done
echo ALLDONE
