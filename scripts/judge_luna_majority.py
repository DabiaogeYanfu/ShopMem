"""5-vote luna majority for every distinct answer of the sol Reader cold-start data (user, 2026-10-10).
Unit = (question_id, prediction): identical answers of one question share their votes. Vote 1 is the
original luna call (its cache entry, no seed); votes 2-5 are fresh calls with seed 201..204 (separate cache
keys). Resumable: answers already in the output are skipped.
    python -m scripts.judge_luna_majority [--workers 8]   -> results/reader_cold_sol_luna5.jsonl"""
import argparse, hashlib, json, threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from memeval.data import load, read_jsonl
from memeval.llm import UsageMeter
from memeval.prompts import judge_messages
from memeval.score import parse_label
from memeval.teacher import LUNA, endpoint

RUNS = ['reader_cold_sol_K20', 'reader_cold_sol_K20_micu', 'reader_cold_sol_K20_yanredo']
OUT = Path('results/reader_cold_sol_luna5.jsonl')
SEEDS = [None, 201, 202, 203, 204]


def pred_hash(p):
    return hashlib.sha256(p.encode()).hexdigest()[:16]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--workers', type=int, default=8)
    a = ap.parse_args()
    _, qs, _ = load('en', 'train', 20)
    Q = {q.question_id: q for q in qs}
    answers = {}
    for run in RUNS:
        for r in read_jsonl(f'runs/{run}/trajectories.jsonl'):
            answers.setdefault((r['question_id'], r['prediction']), 0)
            answers[(r['question_id'], r['prediction'])] += 1
    done = {(r['question_id'], r['pred_hash']) for r in read_jsonl(OUT)} if OUT.exists() else set()
    jobs = [(q, p) for (q, p) in answers if (q, pred_hash(p)) not in done]
    print(f'{len(answers)} distinct answers, {len(jobs)} to judge x {len(SEEDS)} votes', flush=True)
    meter = UsageMeter()
    luna = endpoint(LUNA, '.cache/llm.sqlite', meter, max_tokens=256)
    lock = threading.Lock()

    def one(job):
        qid, pred = job
        q = Q[qid]
        msgs = judge_messages(q.category, q.question, q.reference, pred)
        raws = [luna.complete(msgs, 'judge', **({} if s is None else {'seed': s})).text for s in SEEDS]
        votes = [parse_label(t) for t in raws]
        row = {'question_id': qid, 'category': q.category, 'pred_hash': pred_hash(pred), 'rows': answers[job],
               'votes': votes, 'n_correct': sum(votes), 'majority': sum(votes) >= 3,
               'unanimous': len(set(votes)) == 1, 'raws': raws}
        with lock, open(OUT, 'a') as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + '\n')

    with ThreadPoolExecutor(a.workers) as pool:
        for i, _ in enumerate(pool.map(one, jobs)):
            if i % 500 == 0:
                print(i, json.dumps(meter.snapshot()), flush=True)
    print('DONE', json.dumps(meter.snapshot()), flush=True)


if __name__ == '__main__':
    main()
