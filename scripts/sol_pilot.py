"""sol rescue pilot for the Reader cold start (实验计划_v3 R1, approved 2026-10-09).

sample: stratified draw of no_kept questions (no clean judged-correct luna trajectory among 4) from
    runs/reader_cold_K20, at most --per-category per category -> runs/sol_pilot_K20/question_ids.txt
summary: rescue rate = share of those questions with >= 1 clean (format_ok) correct sol trajectory,
    overall and per category -> results/sol_pilot/{summary.json, rescue_by_category.csv, .pdf, .png}

    python scripts/sol_pilot.py sample
    python -m memeval.reader_cold generate --run-name sol_pilot_K20 --bank-dir runs/topr1_train_K20/banks \
        --pool train --K 20 --n 4 --n-search 2 --teacher-model gpt-6.1-sol --question-ids runs/sol_pilot_K20/question_ids.txt
    python scripts/sol_pilot.py summary
"""
import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from memeval.data import read_jsonl  # noqa: E402

LUNA_RUN = ROOT / 'runs' / 'reader_cold_K20'
RUN = ROOT / 'runs' / 'sol_pilot_K20'
OUT = ROOT / 'results' / 'sol_pilot'


def by_question(path):
    by_q = defaultdict(list)
    for r in read_jsonl(path):
        by_q[r['question_id']].append(r)
    return by_q


def sample(a):
    by_cat = defaultdict(list)
    for qid, rs in sorted(by_question(LUNA_RUN / 'trajectories.jsonl').items()):
        if not any(r['correct'] and r['format_ok'] for r in rs):
            by_cat[rs[0]['category']].append(qid)
    rng = random.Random(a.seed)
    picked = {c: sorted(rng.sample(q, min(a.per_category, len(q)))) for c, q in sorted(by_cat.items())}
    RUN.mkdir(parents=True, exist_ok=True)
    (RUN / 'question_ids.txt').write_text(''.join(q + '\n' for c in picked for q in picked[c]))
    (RUN / 'sample.json').write_text(json.dumps({'per_category': a.per_category, 'seed': a.seed,
                                                 'no_kept': {c: len(q) for c, q in by_cat.items()},
                                                 'picked': {c: len(q) for c, q in picked.items()}}, indent=2))
    print({c: f'{len(picked[c])}/{len(by_cat[c])}' for c in picked}, sum(map(len, picked.values())))


def summary(a):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    by_q = by_question(RUN / 'trajectories.jsonl')
    cats = defaultdict(lambda: {'questions': 0, 'rescued': 0, 'rescued_any': 0, 'traj': 0, 'correct': 0})
    for qid, rs in by_q.items():
        c = cats[rs[0]['category']]
        c['questions'] += 1
        c['traj'] += len(rs)
        c['correct'] += sum(r['correct'] for r in rs)
        c['rescued'] += any(r['correct'] and r['format_ok'] for r in rs)
        c['rescued_any'] += any(r['correct'] for r in rs)
    tot = {k: sum(c[k] for c in cats.values()) for k in ('questions', 'rescued', 'rescued_any', 'traj', 'correct')}
    res = {'questions': tot['questions'], 'trajectories': tot['traj'],
           'rescue_rate': tot['rescued'] / max(tot['questions'], 1),
           'rescue_rate_any_format': tot['rescued_any'] / max(tot['questions'], 1),
           'traj_acc': tot['correct'] / max(tot['traj'], 1), 'threshold': a.threshold,
           'by_category': {k: v | {'rescue_rate': v['rescued'] / v['questions']} for k, v in sorted(cats.items())}}
    res['decision'] = 'add sol trajectories for hard questions' if res['rescue_rate'] > a.threshold else 'luna only'
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'summary.json').write_text(json.dumps(res, indent=2))
    rows = sorted(res['by_category'].items(), key=lambda kv: kv[1]['rescue_rate'])
    with open(OUT / 'rescue_by_category.csv', 'w') as fh:
        fh.write('category,questions,rescued,rescue_rate,traj,correct\n')
        for k, v in rows:
            fh.write(f"{k},{v['questions']},{v['rescued']},{v['rescue_rate']:.4f},{v['traj']},{v['correct']}\n")
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.barh([k for k, _ in rows], [100 * v['rescue_rate'] for _, v in rows], color='#4C72B0')
    for i, (_, v) in enumerate(rows):
        ax.text(100 * v['rescue_rate'] + 1, i, f"{v['rescued']}/{v['questions']}", va='center', fontsize=8)
    ax.axvline(100 * a.threshold, ls='--', c='grey', lw=1)
    ax.set_xlabel('Rescue rate (%): >=1 of 4 gpt-6.1-sol trajectories correct')
    ax.set_title(f"sol rescue on luna no_kept questions (overall {100 * res['rescue_rate']:.1f}%, n={res['questions']})",
                 fontsize=9)
    ax.set_xlim(0, 105)
    fig.tight_layout()
    for ext in ('pdf', 'png'):
        fig.savefig(OUT / f'rescue_by_category.{ext}', dpi=200)
    print(json.dumps({k: v for k, v in res.items() if k != 'by_category'}, indent=2))
    for k, v in rows:
        print(f"{k:28s} {v['rescued']:3d}/{v['questions']:3d}  {100 * v['rescue_rate']:5.1f}%")


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('sample')
    s.add_argument('--per-category', type=int, default=10)
    s.add_argument('--seed', type=int, default=0)
    m = sub.add_parser('summary')
    m.add_argument('--threshold', type=float, default=0.30)
    a = p.parse_args()
    sample(a) if a.cmd == 'sample' else summary(a)
