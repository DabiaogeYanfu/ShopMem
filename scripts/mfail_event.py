"""Event-level M-Fail for the test90 Writer / v1 runs. A required event counts as in memory if any of its
source turns is a source turn of some memory unit (every version, closed ones too), and as retrieved if any
of its source turns is in the question's retrieved provenance. Per answerable question:
correct | memory (an event in no unit) | retrieval (all in memory, one not retrieved) | reasoning."""
import collections, csv, json, sys
from pathlib import Path
from memeval.data import load
hs, qs, gold = load('en', 'test90', 30)
qa = [q for q in qs if q.answerable]
H = ['H_EN_TEST90_K030_001', 'H_EN_TEST90_K030_002', 'H_EN_TEST90_K030_003']
out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
rows = []
for r in ('writer_sft_ep3', 'shopmem_v1'):
    cov = {h: {t for u in map(json.loads, open(f'runs/mfail2_banks_{r}/banks/{h}.jsonl')) for t in u['source_turns']} for h in H}
    P = {x['question_id']: x for x in map(json.loads, open(f'runs/v2test90_K30_{r}/predictions.jsonl'))}
    S = {x['question_id']: x for x in map(json.loads, open(f'runs/v2test90_K30_{r}/scores.jsonl'))}
    for q in qa:
        ev = [gold.event_turns.get(e, ()) for e in q.required_events]
        prov = set(P[q.question_id]['provenance'])
        in_mem = all(any(t in cov[q.history_id] for t in ts) for ts in ev)
        got = all(any(t in prov for t in ts) for ts in ev)
        kind = 'correct' if S[q.question_id]['correct'] else 'memory' if not in_mem else 'retrieval' if not got else 'reasoning'
        rows.append({'method': r, 'question_id': q.question_id, 'category': q.category, 'kind': kind})
with open(out / 'mfail_event.csv', 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
K = ('correct', 'memory', 'retrieval', 'reasoning')
for r in ('writer_sft_ep3', 'shopmem_v1'):
    R = [x for x in rows if x['method'] == r]; c = collections.Counter(x['kind'] for x in R)
    print(f'{r:16s} n={len(R)} ' + '  '.join(f'{k} {100*c[k]/len(R):.1f}' for k in K))
print(f"\n{'category':26s}" + ''.join(f'{m[:6]+"/"+k[:4]:>12s}' for m in ('writer', 'v1') for k in K[1:]))
for cat in sorted({x['category'] for x in rows}):
    line = f'{cat:26s}'
    for r in ('writer_sft_ep3', 'shopmem_v1'):
        R = [x for x in rows if x['method'] == r and x['category'] == cat]; c = collections.Counter(x['kind'] for x in R)
        line += ''.join(f'{100*c[k]/len(R):12.1f}' for k in K[1:])
    print(line)
