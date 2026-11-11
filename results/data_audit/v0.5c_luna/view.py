import json, sys
T = {}
for sp in ['test', 'test_ext']:
    for l in open(f'mloc_1e0/data/generated-EN/{sp}/sessions.jsonl'):
        s = json.loads(l)
        for t in s['turns']: T[t['dialog_id']] = (s['session_start'], t)
def show(ids, w=420):
    for i in ids:
        st, t = T[i]
        txt = t['text']
        print(f"{i} [{t['speaker_role']}/{t['turn_type']}] {t['timestamp'][5:16]}: {txt[:w]}")
if __name__ == '__main__':
    show(sys.argv[1:])
