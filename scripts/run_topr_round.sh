#!/bin/bash
# One offline TOPR round, sampling + rewards (v2 phase 2): the current Writer (vLLM on autoDL-B, reached
# through the lab tunnel localhost:18010) writes every train window -- backbone greedy, N samples at
# temperature T -- plus one luna teacher sample, from shared prefix banks; then every write is scored
# (r_task: luna answers the window's questions over closed_view=rule retrieval, luna judges per item,
# GPT concurrency 8; r_evid; r_ind). Resumable (generation replays the LLM cache, scoring skips done rows).
#   ROUND=1 WRITER=writer_ep3 scripts/run_topr_round.sh      log: ~/logs/topr<ROUND>.log
cd ~/M-locomo
source ~/.config/memeval/secrets.env
export TIKTOKEN_CACHE_DIR=$HOME/.cache/tiktoken
ROUND=${ROUND:-1}; WRITER=${WRITER:-writer_ep3}; K=${K:-20}; N=${N:-8}; T=${T:-1.0}
RUN=topr${ROUND}_train_K${K}
echo "=== generate $RUN ($WRITER, n=$N, T=$T, +1 teacher) $(date)"
~/envs/memeval/bin/python -m memeval.teacher generate --pool train --K $K --n $N --temperature $T --teacher-samples 1 \
    --writer-model $WRITER --run-name $RUN --method-config closed_view=rule 2>&1 | tail -6
echo "=== score $RUN $(date)"
~/envs/memeval/bin/python -m memeval.teacher score --run-name $RUN --answerer luna --row-workers 8 2>&1 | tail -3
echo "ALLDONE $(date)"
