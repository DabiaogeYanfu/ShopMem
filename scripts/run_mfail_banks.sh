#!/bin/bash
# M-Fail (v2 §3.6): rebuild the memory banks of the test90 SFT Writer and v1 runs on all 3 histories
# (writes and answers replay from the LLM cache: same config as the original runs), then memeval.revid
# sorts every answerable question of the ORIGINAL runs (their predictions + luna judgments) into
# correct / memory / retrieval / reasoning. No question limit: --limit-per-category selected questions
# from one history only, so only that history was ingested. Log: ~/logs/mfail2.log
cd ~/M-locomo
export TIKTOKEN_CACHE_DIR=$HOME/.cache/tiktoken
NOTHINK='{"chat_template_kwargs":{"enable_thinking":false}}'
COMMON=(--lang en --pool test90 --K 30 --model Qwen3-8B --base-url http://localhost:8001/v1 --api-key-env ""
        --extra-body "$NOTHINK" --workers 16 --no-judge --answer-format reasoning --max-answer-tokens 1024)
BASE=(embed_base_url=http://localhost:8006/v1 embed_model=Qwen3-Embedding-8B
      rerank_base_url=http://localhost:8007/v1 rerank_model=Qwen3-Reranker-4B)
echo "=== banks writer $(date)"
~/envs/memeval/bin/python -m memeval.run "${COMMON[@]}" --run-name mfail2_banks_writer_sft_ep3 --method shopmem_writer \
    --method-config "${BASE[@]}" writer_model=writer_ep3 writer_base_url=http://localhost:18010/v1 writer_api_key_env= \
    write_workers=16 closed_view=rule "writer_extra_body=$NOTHINK" bank_path=runs/mfail2_banks_writer_sft_ep3/banks 2>&1 | tail -1
echo "=== banks v1 $(date)"
~/envs/memeval/bin/python -m memeval.run "${COMMON[@]}" --run-name mfail2_banks_shopmem_v1 --method shopmem \
    --method-config "${BASE[@]}" bank_path=runs/mfail2_banks_shopmem_v1/banks 2>&1 | tail -1
for r in writer_sft_ep3 shopmem_v1; do echo "=== revid $r $(date)"
  ~/envs/memeval/bin/python -m memeval.revid --run runs/v2test90_K30_$r --banks runs/mfail2_banks_$r/banks > /dev/null
done
echo ALLDONE
