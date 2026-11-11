#!/bin/bash
# Reader gate 1 needs the dev30 banks of the SFT Writer (ep3); runs/dev30_writer_sft_ep3 did not save them.
# Same config as that run plus bank_path: writes and answers replay from the LLM cache (the Writer
# vLLM on :18010 is down, so a cache miss fails loudly instead of rewriting). Log: ~/logs/dev30_banks.log
cd ~/M-locomo
export TIKTOKEN_CACHE_DIR=$HOME/.cache/tiktoken
NOTHINK="{\"chat_template_kwargs\":{\"enable_thinking\":false}}"
~/envs/memeval/bin/python -m memeval.run --lang en --pool dev30 --K 30 --model Qwen3-8B --base-url http://localhost:8001/v1 \
    --api-key-env "" --extra-body "$NOTHINK" --workers 16 --no-judge --answer-format reasoning --max-answer-tokens 1024 \
    --run-name dev30_banks_writer_sft_ep3 --method shopmem_writer \
    --method-config embed_base_url=http://localhost:8006/v1 embed_model=Qwen3-Embedding-8B \
    rerank_base_url=http://localhost:8007/v1 rerank_model=Qwen3-Reranker-4B writer_model=writer_ep3 \
    writer_base_url=http://localhost:18010/v1 writer_api_key_env= write_workers=16 "writer_extra_body=$NOTHINK" \
    bank_path=runs/dev30_banks_writer_sft_ep3/banks 2>&1 | tail -3
echo ALLDONE
