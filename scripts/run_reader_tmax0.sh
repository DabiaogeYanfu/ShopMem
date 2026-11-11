#!/bin/bash
# Reader ablation t_max=0 (no search): the sol SFT Reader answers from the first retrieval only (same
# context as the one-shot pipeline), dev30, same setup as scripts/run_reader_gate1.sh otherwise.
# Splits gate 1 (84.5 vs one-shot 54.4) into search gain vs answer-style gain.
# Log: ~/logs/reader_tmax0.log (ALLDONE)
cd ~/M-locomo
source ~/.config/memeval/secrets.env
export TIKTOKEN_CACHE_DIR=$HOME/.cache/tiktoken
NOTHINK="{\"chat_template_kwargs\":{\"enable_thinking\":false}}"
R=${READER:-reader_sft_sol}
RUN=dev30_tmax0_$R
~/envs/memeval/bin/python -m memeval.run --lang en --pool dev30 --K 30 --model $R --base-url http://localhost:18100/v1 \
    --api-key-env "" --extra-body "$NOTHINK" --workers 16 --no-judge --max-answer-tokens 1024 \
    --run-name $RUN --method shopmem_reader \
    --method-config bank_dir=runs/dev30_banks_req t_max=0 closed_view=rule \
    embed_base_url=http://localhost:8006/v1 embed_model=Qwen3-Embedding-8B \
    rerank_base_url=http://localhost:8007/v1 rerank_model=Qwen3-Reranker-4B 2>&1 | tail -3
~/envs/memeval/bin/python -m memeval.score runs/$RUN --judge-model gpt-6-luna --base-url ${JUDGE_BASE_URL:-https://api.openai.com/v1} \
    --api-key-env JUDGE_API_KEY --cache .cache/llm.sqlite --workers 8 2>&1 | grep "Judge acc"
echo ALLDONE
