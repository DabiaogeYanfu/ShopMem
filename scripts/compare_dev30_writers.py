import json, collections
from math import comb
def load(r): return {x['question_id']: x for x in map(json.loads, open(f'{r}/scores.jsonl'))}
def mcn(A, B):
    k = list(A); b = sum(A[q]['correct'] and not B[q]['correct'] for q in k); c = sum(B[q]['correct'] and not A[q]['correct'] for q in k)
    n = b + c; p = min(1, 2 * sum(comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n); return b, c, p
R = {'v1': 'v2dev30_K30_shopmem_v1', 'SFT': 'dev30_writer_sft_ep3_rule', 'topr1': 'dev30_writer_topr1_rule',
     's200': 'dev30_writer_topr1b_step200_rule', 'final': 'dev30_writer_topr1b_final_rule'}
D = {}
for k, r in R.items():
    try: D[k] = load(r)
    except Exception as e: print('skip', k, e)
for k in D: print(f'{k:6s} acc {100*sum(x["correct"] for x in D[k].values())/len(D[k]):5.1f}  n {len(D[k])}')
for a, b in [('SFT', 's200'), ('SFT', 'final'), ('s200', 'final'), ('v1', 's200')]:
    if a in D and b in D:
        x, y, p = mcn(D[a], D[b]); print(f'{a} vs {b}: {a}-only {x}, {b}-only {y}, p={p:.3g}')
cats = sorted({x['category'] for x in D['SFT'].values()})
cols = [k for k in ('v1', 'SFT', 'topr1', 's200', 'final') if k in D]
print(f"{'category':26s}" + ''.join(f'{c:>7s}' for c in cols))
for c in cats:
    qs = [q for q, x in D['SFT'].items() if x['category'] == c]
    print(f'{c:26s}' + ''.join(f"{100*sum(D[k][q]['correct'] for q in qs)/len(qs):7.1f}" for k in cols))
for k in ('SFT', 's200', 'final'):
    s = json.load(open(f'{R[k]}/summary.json'))
    print(k, 'ctx', round(s['context_tokens_mean']), 'entries', s['memory_entries'], 'ingest_s', round(s['ingest_s_total']))
