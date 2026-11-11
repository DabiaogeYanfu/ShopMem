#!/bin/bash
# cross_role fix check: the SFT Writer (ep3) on dev30 with request_units=true (rule-written units for the
# person requests no unit cites), same config as dev30_writer_sft_ep3_rule otherwise. Writes replay from
# the LLM cache (Writer vLLM down: a miss fails loudly); answers by the 8B; luna judge.
# Log: ~/logs/dev30_request_units.log (ALLDONE)
cd ~/M-locomo
source ~/.config/memeval/secrets.env
export TIKTOKEN_CACHE_DIR=$HOME/.cache/tiktoken
NOTHINK="{\"chat_template_kwargs\":{\"enable_thinking\":false}}"
RUN=${RUN:-dev30_writer_sft_ep3_rule_req}
~/envs/memeval/bin/python -m memeval.run --lang en --pool dev30 --K 30 --model Qwen3-8B --base-url http://localhost:8001/v1 \
    --api-key-env "" --extra-body "$NOTHINK" --workers 16 --no-judge --answer-format reasoning --max-answer-tokens 1024 \
    --run-name $RUN --method shopmem_writer \
    --method-config embed_base_url=http://localhost:8006/v1 embed_model=Qwen3-Embedding-8B \
    rerank_base_url=http://localhost:8007/v1 rerank_model=Qwen3-Reranker-4B writer_model=writer_ep3 \
    writer_base_url=http://localhost:18010/v1 writer_api_key_env= write_workers=16 "writer_extra_body=$NOTHINK" \
    closed_view=rule request_units=true bank_path=runs/$RUN/banks 2>&1 | tail -3
~/envs/memeval/bin/python -m memeval.score runs/$RUN --judge-model gpt-6-luna --base-url ${JUDGE_BASE_URL:-https://api.openai.com/v1} \
    --api-key-env JUDGE_API_KEY --cache .cache/llm.sqlite --workers 8 2>&1 | grep "Judge acc"
echo ALLDONE
