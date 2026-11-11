"""Reader signal gate summary: per category mixed share, greedy acc, sample mean acc, pass@n, search use."""
import json, sys
from collections import defaultdict
rows = [json.loads(l) for l in open(sys.argv[1])]
Q = defaultdict(dict)
cat, srch = {}, defaultdict(list)
for r in rows:
    Q[r['question_id']][r['sample']] = r['correct']
    cat[r['question_id']] = r['category']
    if r['sample'] >= 0:
        srch[r['category']].append(len(r['searches']))
by = defaultdict(list)
for q, s in Q.items():
    samp = [v for k, v in s.items() if k >= 0]
    if len(samp) < 8 or -1 not in s:
        continue
    by[cat[q]].append((s[-1], sum(samp) / len(samp), any(samp), 0 < sum(samp) < len(samp)))
print(f'{"category":26s} {"n":>3s} {"greedy":>6s} {"mean@8":>6s} {"pass@8":>6s} {"mixed":>6s} {"search":>6s}')
allq = []
for c in sorted(by):
    v = by[c]
    allq += v
    m = lambda i: 100 * sum(x[i] for x in v) / len(v)
    print(f'{c:26s} {len(v):3d} {m(0):6.1f} {m(1):6.1f} {m(2):6.1f} {m(3):6.1f} {sum(srch[c]) / len(srch[c]):6.2f}')
m = lambda i: 100 * sum(x[i] for x in allq) / len(allq)
print(f'{"ALL":26s} {len(allq):3d} {m(0):6.1f} {m(1):6.1f} {m(2):6.1f} {m(3):6.1f}')
print(f'gate: mixed share {m(3) / 100:.2f} (>= .30 -> PASS)' + ('  PASS' if m(3) >= 30 else '  FAIL'))
