#!/bin/bash
# after v6: ShopMem v7 = v6 + tool evidence + entity-grouped context (no reranker yet, 0.6B embedding)
while ! grep -q ALLDONE ~/logs/dev390_v6.log; do sleep 30; done
cd ~/M-locomo
source ~/.config/memeval/secrets.env
export TIKTOKEN_CACHE_DIR=$HOME/.cache/tiktoken
COMMON=(--lang zh --pool dev --K 10 --model Qwen3-8B --base-url http://localhost:8001/v1 --api-key-env ""
        --extra-body "{\"chat_template_kwargs\":{\"enable_thinking\":false}}"
        --judge-model gpt-6-luna --judge-base-url ${JUDGE_BASE_URL:-https://api.openai.com/v1} --judge-api-key-env JUDGE_API_KEY
        --answer-format reasoning --max-answer-tokens 1024)
run() { name=$1; shift; echo "=== $name $(date)"; ~/envs/memeval/bin/python -m memeval.run "${COMMON[@]}" --run-name $name "$@" 2>&1 | tail -3; }
run dev390_shopmem_v7_R --method shopmem
echo ALLDONE
