#!/bin/bash
# v2 §3.4 ablations on test90 (EN K=30, 3510 q), same read config / answerer as run_test90_writer.sh.
#   scripts/run_test90_ablation.sh <name> <writer_model> [extra method-config...]
#   add_noop  writer_ep3 actions=add_noop     (w/o SUPERSEDE / EXPIRE: nothing is ever closed)
#   no_rind   writer_no_rind                  (SFT data selected without the r_ind filter / ranking)
#   no_revid  writer_no_revid                 (SFT data selected without r_evid)
# Judging takes ~/.luna_judge.lock so at most one luna judge (concurrency 8) runs at a time.
# The Writer LoRA is served on lab GPU 7 :18010. Answers, then luna judge. Log: ~/logs/test90_abl_<name>.log
cd ~/M-locomo
source ~/.config/memeval/secrets.env
export TIKTOKEN_CACHE_DIR=$HOME/.cache/tiktoken
A=$1; W=$2; shift 2
LOG=~/logs/test90_abl_$A.log
NOTHINK='{"chat_template_kwargs":{"enable_thinking":false}}'
NAME=v2test90_K30_writer_abl_$A
echo "=== $NAME $(date)"
~/envs/memeval/bin/python -m memeval.run --lang en --pool test90 --K 30 --model Qwen3-8B \
    --base-url http://localhost:8001/v1 --api-key-env "" --extra-body "$NOTHINK" --workers 16 --no-judge \
    --answer-format reasoning --max-answer-tokens 1024 --method shopmem_writer --run-name $NAME \
    --method-config embed_base_url=http://localhost:8006/v1 embed_model=Qwen3-Embedding-8B \
      rerank_base_url=http://localhost:8007/v1 rerank_model=Qwen3-Reranker-4B \
      trace_path=runs/$NAME/trace.jsonl writer_model=$W writer_base_url=http://localhost:18010/v1 \
      writer_api_key_env= write_workers=16 closed_view=rule "writer_extra_body=$NOTHINK" "$@" 2>&1 | tail -3
grep -q "done -> .*/runs/$NAME\$" $LOG || { echo "answers not done, skip judge"; exit 1; }
echo "=== judge $NAME $(date)"
flock ~/.luna_judge.lock ~/envs/memeval/bin/python -m memeval.score runs/$NAME --judge-model gpt-6-luna --base-url ${JUDGE_BASE_URL:-https://api.openai.com/v1} \
    --api-key-env JUDGE_API_KEY --cache .cache/llm.sqlite --workers 8 2>&1 | grep "Judge acc"
echo ALLDONE
