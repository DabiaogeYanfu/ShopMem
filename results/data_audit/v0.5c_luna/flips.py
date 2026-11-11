import json, re, collections as C
def rd(p): return {r['question_id']: r for r in map(json.loads, open(p))}
b = rd('v0.5b_luna/gold_audit_en_test+test_ext.jsonl'); c = rd('v0.5c_luna/gold_audit_en_test+test_ext.jsonl')
maj = lambda r: r['verdict'] != 'ok' and r.get('severity') == 'major'
def tmpl(q):  # crude template key: strip the subject (IDs / names with digits)
    t = re.sub(r"[A-Z][A-Za-z0-9_-]*\d[A-Za-z0-9_-]*('s)?", '{S}', q)
    return re.sub(r'\d{4}-\d{2}-\d{2}[^ ,?]*', '{T}', t)
for cat in ['tool_evidence_recall', 'constraint_recall', 'cross_role_dependency', 'conditioned_parameter']:
    ids = [k for k in c if c[k]['category'] == cat]
    tr = C.Counter((maj(b[k]), maj(c[k]), b[k]['question'] == c[k]['question'], b[k]['reference'] == c[k]['reference']) for k in ids)
    print(f'\n== {cat}  (b_major, c_major, same_q, same_gold): count')
    for k, v in sorted(tr.items(), key=lambda x: -x[1]): print('  ', k, v)
    by = C.defaultdict(lambda: [0, 0, 0])
    for k in ids:
        t = tmpl(c[k]['question']); by[t][0] += 1; by[t][1] += maj(c[k]); by[t][2] += maj(b[k])
    print('  per v0.5c template: n, c_major, b_major(same qids)')
    for t, (n, m, mb) in sorted(by.items(), key=lambda x: -x[1][1]): print(f'   {n:3d} {m:3d} {mb:3d}  {t[:110]}')
    print('  c verdicts on major:', C.Counter(c[k]['verdict'] for k in ids if maj(c[k])))
