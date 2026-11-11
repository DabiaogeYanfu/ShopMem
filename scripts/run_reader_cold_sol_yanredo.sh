#!/bin/bash
# Re-generate on yan-gpt (gpt-6.1-sol, chat; luna judges inline as in reader_cold_sol_K20) -> reader_cold_sol_K20_yanredo:
# - the 255 (question, sample) pairs that were suspect in the pre-fix micu run (FormatRetry-resampled glued
#   replies, yan cache replays); their micu re-generations are quarantined first (user decision 2026-10-10)
# - the 69 pairs micu never generated (its quota ran out 2026-10-10 14:08 UTC)
# yan_tail_keys.txt = redo_on_yan_keys.txt + missing_after_quota_keys.txt.
cd ~/M-locomo
set -a; source ~/.config/memeval/secrets.env; set +a
for i in $(seq 1 10); do
  ~/envs/memeval/bin/python -m memeval.reader_cold generate --run-name reader_cold_sol_K20_yanredo \
    --bank-dir runs/topr1_train_K20/banks --pool train --K 20 --n 4 --n-search 2 \
    --teacher-model gpt-6.1-sol --teacher-endpoint yan-gpt --format-hint --format-retries 2 \
    --keys runs/reader_cold_sol_K20_micu/yan_tail_keys.txt --workers 16 --gpt-workers 8 && break
  echo "RETRY $i"; sleep 60
done
echo ALLDONE
