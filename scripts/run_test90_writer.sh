#!/bin/bash
# Final numbers (v2 §3.3, test90 EN K=30, 3 histories, 3510 q): ShopMem-Writer SFT (Qwen3-4B +
# adapter_ep3, vLLM on lab GPU 7 :18010), read config closed_view=rule -- identical to
# dev30_writer_sft_ep3_rule. Answers with the frozen 8B, then per-item luna judging (concurrency 8).
# Resumable. Log: ~/logs/test90_writer.log
cd ~/M-locomo
source ~/.config/memeval/secrets.env
export TIKTOKEN_CACHE_DIR=$HOME/.cache/tiktoken
NOTHINK='{"chat_template_kwargs":{"enable_thinking":false}}'
NAME=v2test90_K30_writer_sft_ep3
echo "=== $NAME $(date)"
~/envs/memeval/bin/python -m memeval.run --lang en --pool test90 --K 30 --model Qwen3-8B \
    --base-url http://localhost:8001/v1 --api-key-env "" --extra-body "$NOTHINK" --workers 16 --no-judge \
    --answer-format reasoning --max-answer-tokens 1024 --method shopmem_writer --run-name $NAME \
    --method-config embed_base_url=http://localhost:8006/v1 embed_model=Qwen3-Embedding-8B \
      rerank_base_url=http://localhost:8007/v1 rerank_model=Qwen3-Reranker-4B \
      trace_path=runs/$NAME/trace.jsonl writer_model=writer_ep3 writer_base_url=http://localhost:18010/v1 \
      writer_api_key_env= write_workers=16 closed_view=rule "writer_extra_body=$NOTHINK" 2>&1 | tail -3
grep -q "done -> .*/runs/$NAME\$" ~/logs/test90_writer.log || { echo "answers not done, skip judge"; exit 1; }
echo "=== judge $NAME $(date)"
~/envs/memeval/bin/python -m memeval.score runs/$NAME --judge-model gpt-6-luna --base-url ${JUDGE_BASE_URL:-https://api.openai.com/v1} \
    --api-key-env JUDGE_API_KEY --cache .cache/llm.sqlite --workers 8 2>&1 | grep "Judge acc"
echo ALLDONE
