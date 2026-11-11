#!/bin/bash
# Same-question check that micu-gpt serves the same gpt-6.1-sol as yan-gpt: re-generate 39 questions
# (3 per category) of reader_cold_sol_K20 on micu-gpt with identical settings, judge with luna on yan-gpt,
# compare paired accuracy / search use / length with the yan rows of the same questions.
cd ~/M-locomo
set -a; source ~/.config/memeval/secrets.env; set +a
~/envs/memeval/bin/python -m memeval.reader_cold generate --run-name endpoint_parity_micu \
  --bank-dir runs/topr1_train_K20/banks --pool train --K 20 --n 4 --n-search 2 \
  --teacher-model gpt-6.1-sol --teacher-endpoint micu-gpt --format-hint --format-retries 2 \
  --no-judge --question-ids results/endpoint_parity_ids.txt --workers 8 --gpt-workers 8
~/envs/memeval/bin/python -m memeval.reader_cold judge --run-name endpoint_parity_micu --judge-model gpt-6-luna --judge-endpoint yan-gpt --workers 8
echo ALLDONE
