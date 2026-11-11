#!/bin/bash
# RQ3 efficiency (v2 §3.5): real (uncached) write / retrieve / answer cost of ShopMem-Writer SFT vs v1 on one
# test90 history (H_EN_TEST90_K030_001, 1725 turns). Fresh, empty LLM / embedding / rerank caches, so every
# call reaches its model. Same configs as the test90 runs. Run one after the other (they share the embedding
# and reranker services). The Writer's GPU 7 vLLM KV-cache usage is sampled from its log afterwards.
# Log: ~/logs/efficiency.log
cd ~/M-locomo
export TIKTOKEN_CACHE_DIR=$HOME/.cache/tiktoken
NOTHINK='{"chat_template_kwargs":{"enable_thinking":false}}'
C=.cache/eff_$(date +%Y%m%d_%H%M%S)
COMMON=(--lang en --pool test90 --K 30 --model Qwen3-8B --base-url http://localhost:8001/v1 --api-key-env ""
        --extra-body "$NOTHINK" --workers 16 --no-judge --answer-format reasoning --max-answer-tokens 1024
        --max-histories 1 --limit-per-category 10)
BASE=(embed_base_url=http://localhost:8006/v1 embed_model=Qwen3-Embedding-8B
      rerank_base_url=http://localhost:8007/v1 rerank_model=Qwen3-Reranker-4B)
echo "=== writer $(date -u +%FT%TZ)"
~/envs/memeval/bin/python -m memeval.run "${COMMON[@]}" --cache ${C}_writer_llm.sqlite --run-name eff_writer_sft_ep3 \
    --method shopmem_writer --method-config "${BASE[@]}" embed_cache=${C}_writer_emb.sqlite rerank_cache=${C}_writer_rr.sqlite \
    writer_model=writer_ep3 writer_base_url=http://localhost:18010/v1 writer_api_key_env= write_workers=16 \
    closed_view=rule "writer_extra_body=$NOTHINK" 2>&1 | tail -1
echo "=== writer_end $(date -u +%FT%TZ)"
echo "=== v1 $(date -u +%FT%TZ)"
~/envs/memeval/bin/python -m memeval.run "${COMMON[@]}" --cache ${C}_v1_llm.sqlite --run-name eff_shopmem_v1 \
    --method shopmem --method-config "${BASE[@]}" embed_cache=${C}_v1_emb.sqlite rerank_cache=${C}_v1_rr.sqlite 2>&1 | tail -1
echo "=== v1_end $(date -u +%FT%TZ)"
echo ALLDONE
