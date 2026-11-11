#!/bin/bash
# After judge_queue_K30.sh ends: re-extract answers with the fixed parse_answer (changed rows get
# re-judged), then judge every main_K30 run that still has unjudged rows (a judge step that
# crashed). One judge job at a time, 4 workers, so GPT concurrency stays at 4. Full error output
# goes to ~/logs/judge_retry_K30.err; a failed attempt waits 10 min before retrying.
while ! grep -q ALLDONE ~/logs/judge_K30.log; do sleep 60; done
cd ~/M-locomo
source ~/.config/memeval/secrets.env
export TIKTOKEN_CACHE_DIR=$HOME/.cache/tiktoken
PY=~/envs/memeval/bin/python
$PY evaluation/reparse_answers.py runs/main_K30_*
unjudged() { $PY -c "import json,sys; sys.exit(0 if any(json.loads(x)['correct'] is None for x in open('$1/scores.jsonl')) else 1)"; }
for attempt in 1 2 3 4 5; do
  todo=$(for d in runs/main_K30_*; do [ -f $d/scores.jsonl ] && unjudged $d && echo $d; done)
  [ -z "$todo" ] && break
  for d in $todo; do
    echo "=== judge $d (attempt $attempt) $(date)"
    if $PY -m memeval.score $d --judge-model gpt-6-luna --base-url ${JUDGE_BASE_URL:-https://api.openai.com/v1} \
        --api-key-env JUDGE_API_KEY --cache .cache/llm.sqlite --workers 4 >> ~/logs/judge_retry_K30.err 2>&1; then
      grep -h "Judge acc" $d/summary.md
    else
      echo "  failed, see judge_retry_K30.err"; sleep 600
    fi
  done
done
echo ALLDONE
