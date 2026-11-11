"""Re-judge the predictions whose verdict conflicts with an identical prediction of the same question
(luna samples even at temperature 0; the cache keeps only the last call). 3 fresh luna calls each
(seed 101..103 separates the cache keys), majority label -> runs/<run>/judge_majority_override.jsonl."""
import collections, json
from pathlib import Path
from memeval.data import load, read_jsonl
from memeval.llm import UsageMeter
from memeval.prompts import judge_messages
from memeval.score import parse_label
from memeval.teacher import LUNA, endpoint
runs = {'yan': 'reader_cold_sol_K20', 'micu': 'reader_cold_sol_K20_micu', 'yanredo': 'reader_cold_sol_K20_yanredo'}
_, qs, _ = load('en', 'train', 20); Q = {q.question_id: q for q in qs}
L = {(v['question_id'], v['sample']): v['correct'] for v in read_jsonl('runs/reader_cold_sol_K20_micu/judge_gpt-6-luna.jsonl')}
rows = [(n, r, L[(r['question_id'], r['sample'])] if n == 'micu' else r['correct'])
        for n, d in runs.items() for r in read_jsonl(f'runs/{d}/trajectories.jsonl')]
by = collections.defaultdict(list)
for n, r, c in rows:
    by[(r['question_id'], r['prediction'])].append((n, r, c))
groups = [v for v in by.values() if len({c for *_, c in v}) > 1]
luna = endpoint(LUNA, '.cache/llm.sqlite', UsageMeter(), max_tokens=256)
out = []
for g in groups:
    _, r0, _ = g[0]
    q = Q[r0['question_id']]
    msgs = judge_messages(q.category, q.question, q.reference, r0['prediction'])
    labels = [parse_label(luna.complete(msgs, 'judge', seed=s).text) for s in (101, 102, 103)]
    maj = sum(labels) >= 2
    for n, r, c in g:
        out.append({'run': runs[n], 'question_id': r['question_id'], 'sample': r['sample'], 'old': c,
                    'votes': labels, 'correct': maj})
    print(r0['question_id'], 'old', [c for *_, c in g], 'votes', labels, '-> majority', maj)
Path('results/reader_cold_sol_rejudge_conflicts.json').write_text(json.dumps(out, indent=2))
