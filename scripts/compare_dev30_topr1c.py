"""dev30 comparison of the topr1c (flat windows filtered, shift 0.05) checkpoints vs SFT ep3, topr1b step200 and v1:
accuracy, McNemar, per category, context size. Run from the repo root: python scripts/compare_dev30_topr1c.py"""
import json
from math import comb
def load(r): return {x['question_id']: x for x in map(json.loads, open(f'runs/{r}/scores.jsonl'))}
def mcn(A, B):
    k = list(A); b = sum(A[q]['correct'] and not B[q]['correct'] for q in k); c = sum(B[q]['correct'] and not A[q]['correct'] for q in k)
    n = b + c; p = min(1, 2 * sum(comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n); return b, c, p
R = {'v1': 'v2dev30_K30_shopmem_v1', 'SFT': 'dev30_writer_sft_ep3_rule', 'b200': 'dev30_writer_topr1b_step200_rule',
     'c200': 'dev30_writer_topr1c_step200_rule', 'cfin': 'dev30_writer_topr1c_final_rule'}
D = {k: load(r) for k, r in R.items()}
for k in D: print(f'{k:5s} acc {100*sum(x["correct"] for x in D[k].values())/len(D[k]):5.1f}  n {len(D[k])}')
for a, b in [('SFT', 'c200'), ('SFT', 'cfin'), ('c200', 'cfin'), ('b200', 'c200'), ('v1', 'c200'), ('v1', 'cfin')]:
    x, y, p = mcn(D[a], D[b]); print(f'{a} vs {b}: {a}-only {x}, {b}-only {y}, p={p:.3g}')
cats = sorted({x['category'] for x in D['SFT'].values()})
print(f"{'category':26s}" + ''.join(f'{c:>7s}' for c in D))
for c in cats:
    qs = [q for q, x in D['SFT'].items() if x['category'] == c]
    print(f'{c:26s}' + ''.join(f"{100*sum(D[k][q]['correct'] for q in qs)/len(qs):7.1f}" for k in D))
for k in ('SFT', 'b200', 'c200', 'cfin'):
    s = json.load(open(f'runs/{R[k]}/summary.json'))
    print(k, 'ctx', round(s['context_tokens_mean']), 'entries', s['memory_entries'])
