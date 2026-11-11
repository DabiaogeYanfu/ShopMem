"""Gate verdict on runs/reward_gate/replay.jsonl (pre-registered criteria):
G1 validity: within question slot (same window+question, varying sample), P(correct|ev_hit=1) - P(correct|ev_hit<1) >= .15
G2 signal  : share of qa windows flat under r_ret (mean ev_hit over attached questions) <= .30
G3 weak cat: share of cross_role / multi_hop slots where samples split on ev_hit >= .30"""
import collections, json, sys
import numpy as np
rows = [json.loads(l) for l in open(sys.argv[1])]
W = collections.defaultdict(list)
for r in rows:
    W[r['history_id'], r['window']].append(r)
slots = collections.defaultdict(list)  # (h,w,qid) -> [(ev_hit, ev_in_mem, correct)]
for (h, w), S in W.items():
    for r in S:
        for q in r['questions']:
            slots[h, w, q['question_id'], q['category']].append((q['ev_hit'], q['ev_in_mem'], q['correct']))
# G1: pooled within-slot difference, only slots with both hit and miss
d, n1, cat_d = [], 0, collections.defaultdict(list)
for k, v in slots.items():
    hit = [c for e, m, c in v if e >= 1 and c is not None]; miss = [c for e, m, c in v if e < 1 and c is not None]
    if hit and miss:
        d.append(np.mean(hit) - np.mean(miss)); cat_d[k[3]].append(d[-1])
g1 = float(np.mean(d))
# G2
flat = [np.ptp([np.mean([q['ev_hit'] for q in r['questions']]) for r in S]) < 1e-6 for S in W.values()]
flat_mem = [np.ptp([np.mean([q['ev_in_mem'] for q in r['questions']]) for r in S]) < 1e-6 for S in W.values()]
g2 = float(np.mean(flat))
# G3 + per-category
print(f"{'category':26s}{'slots':>6s}{'hit_split':>10s}{'mem_split':>10s}{'P(hit)':>8s}{'P(c|hit)':>9s}{'P(c|miss)':>10s}{'dslot':>8s}")
split = {}
for cat in sorted({k[3] for k in slots}):
    S = [v for k, v in slots.items() if k[3] == cat]
    allv = [x for v in S for x in v]
    hs = np.mean([len({e >= 1 for e, m, c in v}) > 1 for v in S]); split[cat] = hs
    ms = np.mean([len({m >= 1 for e, m, c in v}) > 1 for v in S])
    ph = np.mean([e >= 1 for e, m, c in allv])
    ch = [c for e, m, c in allv if e >= 1 and c is not None]; cm = [c for e, m, c in allv if e < 1 and c is not None]
    f = lambda x: f'{np.mean(x):.2f}' if x else '  - '
    print(f"{cat:26s}{len(S):6d}{hs:10.2f}{ms:10.2f}{ph:8.2f}{f(ch):>9s}{f(cm):>10s}{f(cat_d[cat]):>8s}")
g3 = min(split['cross_role_dependency'], split['multi_hop_event'])
print(f'\nqa windows {len(W)}, samples {len(rows)}; slots with hit/miss split {len(d)}')
print(f'flat windows under event in-memory reward: {np.mean(flat_mem):.2f}')
print(f'G1 validity  dP(correct) within slot = {g1:.3f}  (>= .15)  -> {"PASS" if g1 >= .15 else "FAIL"}')
print(f'G2 signal    flat qa windows        = {g2:.2f}   (<= .30)  -> {"PASS" if g2 <= .30 else "FAIL"}')
print(f'G3 weak cats min split share        = {g3:.2f}   (>= .30)  -> {"PASS" if g3 >= .30 else "FAIL"}')
