#!/bin/bash
cd ~/M-locomo; set -a; source ~/.config/memeval/secrets.env; set +a
for i in $(seq 1 10); do ~/envs/memeval/bin/python -m scripts.judge_luna_majority --workers 8 && break; echo "RETRY $i"; sleep 60; done
echo ALLDONE
