"""Offline reward gate, stage A: does a retrieval-hit reward carry more learning signal than the current
r_task-based reward on the topr1 rollouts? Uses only rewards.jsonl (no model calls).
Per window (qa role, writer samples only + teacher), compares
  cur  = r_task + .5 r_ind + .5 r_evid           (what TOPR trained on)
  ret  = mean over attached questions of turn-level evidence_retrieved (deterministic retrieval)
"""
import collections, json, sys
import numpy as np
run = sys.argv[1] if len(sys.argv) > 1 else 'runs/topr1_train_K20'
R = collections.defaultdict(dict)
for r in map(json.loads, open(f'{run}/rewards.jsonl')):
    if r['answerer'] == 'luna':
        R[(r['history_id'], r['window'])][r['sample']] = r
def cur(r): return (r.get('r_task') or 0) + .5 * (r['r_ind'] or 0) + .5 * (r['r_evid'] or 0) if r.get('r_task') is not None else .5 * (r['r_ind'] or 0) + .5 * (r['r_evid'] or 0)
def ret(r): return np.mean([q['evidence_retrieved'] for q in r['questions']]) if r['questions'] else None
roles = collections.Counter()
flat = collections.Counter(); spread = collections.defaultdict(list); corr = []; best_diff = []
slot = collections.defaultdict(lambda: collections.Counter())
for (h, j), S in R.items():
    rows = [S[k] for k in sorted(S)]
    role = rows[0]['role']; roles[role] += 1
    c = [cur(r) for r in rows]
    spread['cur', role].append(max(c) - min(c))
    if role != 'qa':
        continue
    t = [r['r_task'] for r in rows]; g = [ret(r) for r in rows]
    spread['ret', role].append(max(g) - min(g)); spread['task', role].append(max(t) - min(t))
    if np.std(g) > 0 and np.std(t) > 0:
        corr.append(np.corrcoef(g, t)[0, 1])
    best_diff.append(int(np.argmax(g)) != int(np.argmax(c)))
    nq = len(rows[0]['questions'])
    for i in range(nq):
        cat = rows[0]['questions'][i]['category']
        ok = [r['questions'][i]['correct'] for r in rows]; rt = [r['questions'][i]['evidence_retrieved'] for r in rows]
        slot[cat]['n'] += 1
        slot[cat]['task_signal'] += len(set(ok)) > 1
        slot[cat]['ret_signal'] += max(rt) - min(rt) > 1e-9
        slot[cat]['ret_full_any'] += max(rt) >= 1
print('windows by role', dict(roles))
for k, v in sorted(spread.items()):
    v = np.array(v); print(f'{k[0]:5s} {k[1]:9s} n={len(v):3d} flat(<1e-6) {np.mean(v < 1e-6):.2f}  spread>0.1 {np.mean(v > .1):.2f}  median {np.median(v):.3f}')
print(f'qa windows: within-window corr(ret, r_task) mean {np.nanmean(corr):.2f} (n={len(corr)}, >0 share {np.mean(np.array(corr) > 0):.2f})')
print(f'qa windows: argmax(ret) != argmax(cur) in {np.mean(best_diff):.2f}')
print(f"\n{'category':26s}{'slots':>6s}{'task_sig':>9s}{'ret_sig':>8s}{'ret=1 any':>10s}")
for cat, c in sorted(slot.items()):
    print(f"{cat:26s}{c['n']:6d}{c['task_signal']/c['n']:9.2f}{c['ret_signal']/c['n']:8.2f}{c['ret_full_any']/c['n']:10.2f}")
