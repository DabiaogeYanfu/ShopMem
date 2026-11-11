#!/bin/bash
# request_units on the test90 Writer runs (main SFT Writer + the 3 ablation Writers): same configs as
# run_test90_writer.sh / run_test90_ablation.sh plus request_units=true and bank_path. Writes replay from
# the LLM cache (no Writer vLLM: a miss fails loudly), so only role questions get a new context; every
# other answer and verdict replays from cache. luna judge (lock shared with the other judge scripts).
# Log: ~/logs/test90_request_units.log (ALLDONE)
cd ~/M-locomo
source ~/.config/memeval/secrets.env
export TIKTOKEN_CACHE_DIR=$HOME/.cache/tiktoken
NOTHINK="{\"chat_template_kwargs\":{\"enable_thinking\":false}}"
for spec in "writer_sft_ep3|writer_ep3|" "writer_abl_add_noop|writer_ep3|actions=add_noop" \
            "writer_abl_no_rind|writer_no_rind|" "writer_abl_no_revid|writer_no_revid|"; do
  IFS="|" read -r name W extra <<< "$spec"
  NAME=v2test90_K30_${name}_req
  echo "=== $NAME $(date)"
  ~/envs/memeval/bin/python -m memeval.run --lang en --pool test90 --K 30 --model Qwen3-8B \
      --base-url http://localhost:8001/v1 --api-key-env "" --extra-body "$NOTHINK" --workers 16 --no-judge \
      --answer-format reasoning --max-answer-tokens 1024 --method shopmem_writer --run-name $NAME \
      --method-config embed_base_url=http://localhost:8006/v1 embed_model=Qwen3-Embedding-8B \
        rerank_base_url=http://localhost:8007/v1 rerank_model=Qwen3-Reranker-4B \
        writer_model=$W writer_base_url=http://localhost:18010/v1 writer_api_key_env= write_workers=16 \
        closed_view=rule "writer_extra_body=$NOTHINK" request_units=true bank_path=runs/$NAME/banks $extra 2>&1 | tail -1
  [ -f runs/$NAME/predictions.jsonl ] || { echo "FAILED $NAME"; continue; }
  python3 -c "import json;u=json.load(open(\"runs/$NAME/usage.json\"));print({k:(v[\"calls\"],v[\"cached_calls\"]) for k,v in u.items()})"
  flock ~/.luna_judge.lock ~/envs/memeval/bin/python -m memeval.score runs/$NAME --judge-model gpt-6-luna \
      --base-url ${JUDGE_BASE_URL:-https://api.openai.com/v1} --api-key-env JUDGE_API_KEY --cache .cache/llm.sqlite --workers 8 2>&1 | grep "Judge acc"
done
echo ALLDONE
