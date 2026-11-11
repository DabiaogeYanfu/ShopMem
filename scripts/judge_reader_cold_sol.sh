#!/bin/bash
# Judge the sol Reader trajectories while generation is still running: re-runs `reader_cold judge`
# (resumable, only new rows) every 10 min until the generation log says ALLDONE, then one final pass.
# usage: judge_reader_cold_sol.sh RUN JUDGE_MODEL ENDPOINT WORKERS GEN_LOG
cd ~/M-locomo
set -a; source ~/.config/memeval/secrets.env; set +a
RUN=$1; MODEL=$2; EP=$3; W=$4; GENLOG=$5
while true; do
  fin=0; [ -z "$GENLOG" ] || grep -q ALLDONE "$GENLOG" && fin=1
  ~/envs/memeval/bin/python -m memeval.reader_cold judge --run-name $RUN --judge-model $MODEL --judge-endpoint $EP --workers $W
  [ $fin = 1 ] && break
  sleep 600
done
echo ALLDONE
