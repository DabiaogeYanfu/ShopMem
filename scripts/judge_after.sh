#!/bin/bash
# Judge a run once its answers are done:  scripts/judge_after.sh <run name> <answers log>
# Takes ~/.luna_judge.lock, so at most one luna judge (concurrency 8) runs at a time. Used for runs whose
# original wrapper was stopped (test90 simplemem, abl_add_noop): their answer processes now print into
# an orphaned `tail -3`, so "done" = the answer process has exited and predictions.jsonl has all 3510
# rows. memeval.score re-scores everything (scores.jsonl caches judgments), so it does not matter that
# memeval.run's own no-judge scoring may still be writing summary.json. Appends to the log given.
cd ~/M-locomo; source ~/.config/memeval/secrets.env; export TIKTOKEN_CACHE_DIR=$HOME/.cache/tiktoken
NAME=$1; LOG=$2
while pgrep -f "run-name $NAME" > /dev/null; do sleep 60; done
[ "$(wc -l < runs/$NAME/predictions.jsonl)" = 3510 ] || { echo "answers of $NAME stopped before 3510 rows"; exit 1; }
echo "=== judge $NAME $(date)"
flock ~/.luna_judge.lock ~/envs/memeval/bin/python -m memeval.score runs/$NAME --judge-model gpt-6-luna \
    --base-url ${JUDGE_BASE_URL:-https://api.openai.com/v1} --api-key-env JUDGE_API_KEY --cache .cache/llm.sqlite --workers 8 2>&1 | grep "Judge acc"
echo ALLDONE
