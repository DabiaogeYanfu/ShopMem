import json, random, sys
exec(open('view.py').read().split("if __name__")[0])
Q = {}
for sp in ['test', 'test_ext']:
    for l in open(f'mloc_1e0/data/generated-EN/{sp}/questions.jsonl'):
        q = json.loads(l); Q[q['question_id']] = q
A = {r['question_id']: r for r in map(json.loads, open('v0.5c_luna/gold_audit_en_test+test_ext.jsonl'))}
maj = lambda r: r['verdict'] != 'ok' and r.get('severity') == 'major'
cat, pat, nflag, nok, seed = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), 7
ids = [k for k, r in A.items() if r['category'] == cat and (pat == '*' or pat in r['question'])]
fl = sorted(k for k in ids if maj(A[k])); ok = sorted(k for k in ids if not maj(A[k]))
random.seed(seed); pick = random.sample(fl, min(nflag, len(fl))) + random.sample(ok, min(nok, len(ok)))
print(f'pool {len(ids)}: flagged {len(fl)}, ok {len(ok)}')
for k in pick:
    r, q = A[k], Q[k]
    print(f"\n######## {k} [{'FLAG '+r['verdict'] if maj(r) else 'OK'}]\nQ: {r['question']}\nGOLD: {r['reference']}")
    if maj(r): print('LUNA:', r['explanation'][:500])
    for e in q['evidence']:
        t = T[e][1]; txt = t['text']
        if t['turn_type'] == 'tool_observation':
            o = json.loads(txt); txt = f"<{o['tool']} measured_at={o['measured_at']} result={o['result']}> {o['message']}"
        print(f"  {e[12:]} {t['turn_type'][:12]} {t['timestamp'][5:16]}: {txt[:330]}")
