"""Same-question parity of gpt-6.1-sol on micu-gpt vs yan-gpt: python -m scripts.endpoint_parity"""
import json, collections, statistics as st, random
from memeval.data import read_jsonl
ids = set(open('results/endpoint_parity_ids.txt').read().split())
Y = {(r['question_id'], r['sample']): r for r in read_jsonl('runs/reader_cold_sol_K20/trajectories.jsonl') if r['question_id'] in ids}
M = {(r['question_id'], r['sample']): r for r in read_jsonl('runs/endpoint_parity_micu/trajectories.jsonl')}
J = {(r['question_id'], r['sample']): r['correct'] for r in read_jsonl('runs/endpoint_parity_micu/judge_gpt-6-luna.jsonl')}
keys = sorted(set(Y) & set(M))
yq = collections.defaultdict(list); mq = collections.defaultdict(list)
for k in keys:
    yq[k[0]].append(Y[k]['correct']); mq[k[0]].append(J[k])
qs = sorted(yq)
ya = [st.mean(yq[q]) for q in qs]; ma = [st.mean(mq[q]) for q in qs]
diff = [m - y for y, m in zip(ya, ma)]
rng = random.Random(0)
boot = sorted(st.mean(rng.choices(diff, k=len(diff))) for _ in range(10000))
def words(r): return len(str(r['prediction']).split())
out = {
    'questions': len(qs), 'trajectories': len(keys),
    'acc_yan': round(st.mean(ya), 4), 'acc_micu': round(st.mean(ma), 4),
    'acc_diff_micu_minus_yan': round(st.mean(diff), 4), 'diff_95ci': [round(boot[250], 4), round(boot[9750], 4)],
    'questions_better_on': {'yan': sum(d < 0 for d in diff), 'micu': sum(d > 0 for d in diff), 'tie': sum(d == 0 for d in diff)},
    'format_ok': {'yan': sum(Y[k]['format_ok'] for k in keys), 'micu': sum(M[k]['format_ok'] for k in keys)},
    'hinted_searched': {'yan': sum(bool(Y[k]['searches']) for k in keys if Y[k]['hinted']), 'micu': sum(bool(M[k]['searches']) for k in keys if M[k]['hinted']), 'of': sum(Y[k]['hinted'] for k in keys)},
    'plain_searched': {'yan': sum(bool(Y[k]['searches']) for k in keys if not Y[k]['hinted']), 'micu': sum(bool(M[k]['searches']) for k in keys if not M[k]['hinted'])},
    'pred_words_median': {'yan': st.median(words(Y[k]) for k in keys), 'micu': st.median(words(M[k]) for k in keys)},
    'prompt_tokens_per_traj_median': {'yan': st.median(Y[k]['prompt_tokens'] for k in keys), 'micu': st.median(M[k]['prompt_tokens'] for k in keys)},
}
cat = collections.defaultdict(lambda: [[], []])
for q, y, m in zip(qs, ya, ma):
    c = Y[(q, 0)]['category']; cat[c][0].append(y); cat[c][1].append(m)
out['by_category'] = {c: {'yan': round(st.mean(v[0]), 2), 'micu': round(st.mean(v[1]), 2)} for c, v in sorted(cat.items())}
json.dump(out, open('results/endpoint_parity.json', 'w'), indent=2)
print(json.dumps(out, indent=1))
