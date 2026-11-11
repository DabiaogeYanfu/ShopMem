import json, collections as C
for v in ['v0.5b_luna', 'v0.5c_luna']:
    rows = [json.loads(l) for l in open(f'{v}/scenario_audit_en_test+test_ext.jsonl')]
    iss = [(r['scenario_id'], i) for r in rows for i in r['issues'] if i['type'] == 'implausible' and i['severity'] == 'major']
    print(f'#### {v}: {len(iss)} issues in {len({s for s,_ in iss})} scenarios')
    if v == 'v0.5c_luna':
        for s, i in iss: print(f"- {s} {i['turn_ids']}: {i['explanation']}")
