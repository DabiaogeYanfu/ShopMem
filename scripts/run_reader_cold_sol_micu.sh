#!/bin/bash
# Reader cold start, gpt-6.1-sol teacher on the micu-gpt relay while yan-gpt is out of quota:
# same settings as run_reader_cold_sol.sh, but no judge (luna is on yan-gpt only; judge these rows later),
# and the (question, sample) pairs already in reader_cold_sol_K20 are skipped. Resumable, retries on crash.
cd ~/M-locomo
set -a; source ~/.config/memeval/secrets.env; set +a
for i in $(seq 1 10); do
  ~/envs/memeval/bin/python -m memeval.reader_cold generate --run-name reader_cold_sol_K20_micu \
    --bank-dir runs/topr1_train_K20/banks --pool train --K 20 --n 4 --n-search 2 \
    --teacher-model gpt-6.1-sol --teacher-endpoint micu-gpt --format-hint --format-retries 2 \
    --no-judge --exclude-run reader_cold_sol_K20 --workers 24 --gpt-workers 16 && break
  echo "RETRY $i"; sleep 60
done
echo ALLDONE
