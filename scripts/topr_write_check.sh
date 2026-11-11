#!/bin/bash
# Cheap write-side check of TOPR checkpoints on dev30 K30 (no GPT): each adapter, served on autoDL-B as a LoRA
# named <prefix>_<ckpt> through the lab tunnel localhost:18010, writes all 158 windows; only 1 question per
# category is answered (8B, no judge). Then summarise actions / r_ind / open entries per checkpoint.
#   PREFIX=topr1b CKPTS="step50 step100 ... final" scripts/topr_write_check.sh      log: ~/logs/<prefix>_write_check.log
cd ~/M-locomo
source ~/.config/memeval/secrets.env
export TIKTOKEN_CACHE_DIR=$HOME/.cache/tiktoken
NOTHINK='{"chat_template_kwargs":{"enable_thinking":false}}'
for c in $CKPTS; do name=wcheck_${PREFIX}_$c
  echo "=== $name $(date)"
  rm -rf runs/$name
  ~/envs/memeval/bin/python -m memeval.run --lang en --pool dev30 --K 30 --model Qwen3-8B --base-url http://localhost:8001/v1 \
      --api-key-env "" --extra-body "$NOTHINK" --workers 16 --no-judge --answer-format reasoning --max-answer-tokens 1024 \
      --limit-per-category 1 --method shopmem_writer --run-name $name --method-config \
      embed_base_url=http://localhost:8006/v1 embed_model=Qwen3-Embedding-8B rerank_base_url=http://localhost:8007/v1 \
      rerank_model=Qwen3-Reranker-4B closed_view=rule trace_path=runs/$name/trace.jsonl \
      writer_model=${PREFIX}_$c writer_base_url=http://localhost:18010/v1 writer_api_key_env= write_workers=16 \
      writer_extra_body=$NOTHINK 2>&1 | tail -1
done
~/envs/memeval/bin/python - "$PREFIX" $CKPTS <<'PY'
import json, sys, collections
prefix, ckpts = sys.argv[1], sys.argv[2:]
runs = [('sft_ep3', 'runs/dev30_writer_sft_ep3_rule')] + [(c, f'runs/wcheck_{prefix}_{c}') for c in ckpts]
print(f"{'ckpt':10s} {'ADD':>5s} {'SUPER':>5s} {'EXPIRE':>6s} {'NOOP':>5s} {'open':>5s} {'rind_all':>8s}  top violations")
for c, r in runs:
    T = [json.loads(l) for l in open(f'{r}/trace.jsonl')]
    op = collections.Counter(a['kind'] for t in T for a in t['actions'])
    v = collections.Counter(x[0] for t in T for x in t['rind']['violations'])
    s = json.load(open(f'{r}/summary.json'))
    ap = sum(t['rind']['all_pass'] for t in T) / len(T)
    print(f"{c:10s} {op['ADD']:5d} {op['SUPERSEDE']:5d} {op['EXPIRE']:6d} {op['NOOP']:5d} {s['memory_entries']:5d} {ap:8.3f}  {v.most_common(3)}")
PY
echo ALLDONE
