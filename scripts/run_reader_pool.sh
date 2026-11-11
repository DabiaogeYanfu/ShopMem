#!/bin/bash
# GRPO question pool: the sol SFT Reader x G=8 on train (banks with request units), luna judge.
# Two shards, one per Reader vLLM on autoDL-B (GPU0 :18100, GPU1 :18101; lab tunnels), then a merge
# run writes runs/reader_grpo_pool/pool.json. Resumable. Log: ~/logs/reader_grpo_pool.log (ALLDONE / FAILED)
cd ~/M-locomo
source ~/.config/memeval/secrets.env
export TIKTOKEN_CACHE_DIR=$HOME/.cache/tiktoken
P="~/envs/memeval/bin/python -m memeval.reader_rl pool --run-name reader_grpo_pool --reader-model reader_sft_sol
   --bank-dir runs/train_K20_banks_req --G 8 --workers 32 --gpt-workers 8"
eval $P --reader-base-url http://localhost:18100/v1 --shard 0/2 > ~/logs/reader_grpo_pool.s0.log 2>&1 &
eval $P --reader-base-url http://localhost:18101/v1 --shard 1/2 > ~/logs/reader_grpo_pool.s1.log 2>&1 &
wait
eval $P --reader-base-url http://localhost:18100/v1 && echo ALLDONE || echo FAILED
