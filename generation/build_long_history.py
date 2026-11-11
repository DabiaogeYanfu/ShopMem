"""Build M-LoCoMo-Long: plant-level histories that interleave K scenarios.

A single generated scenario is only ~1k tokens, which fits any context window and
makes long-term memory unnecessary. This script merges K scenarios into one
chronological interaction stream (the same agent serving several lines of a plant),
so every question must be answered against a history that also contains K-1
distractor scenarios -- same templates and domains, different entities.

Design choices:
- Scenario pools are split by the original split so dev stays a tuning set:
  `eval` = train + test (90 scenarios, the method is training-free), `dev` = dev (10),
  `dev30` = dev + dev_ext (30, for tuning at K=30), `test90` = test + test_ext (90 scenarios
  never used for training, the K=30 evaluation pool once a method is trained on train),
  `train` = train (80 scenarios, ShopMem-Writer training; 80 is not a multiple of 30, so
  K = 10 / 20 / 40: 20 for teacher traces and SFT, 40 -- at least the evaluation's distractor
  load -- for the last TOPR round, 10 for debugging).
- Groups are domain-stratified (round-robin over shuffled per-domain lists), so each
  history mixes the four domains and same-domain distractors scale with K.
- Sessions are interleaved as whole units, sorted by (session_start, session_id).
  Original timestamps are kept untouched: turn text quotes clock times such as
  "检测时间08:13", so shifting timestamps would contradict the dialogue. Up to 5
  sessions may therefore share a start time (parallel lines in the same plant).
- Questions are copied unchanged plus `history_id` / `K`. `scenario_id` is kept on
  sessions for analysis only; memory systems must not read it at ingestion time.

Output: data/long/<lang>/<pool>/K<k>/{histories,questions}.jsonl
"""
import argparse
import json
import random
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'data'
LANGS = {'zh': 'generated', 'en': 'generated-EN'}
# dev30 = dev + dev_ext (30 held-out scenarios): tuning at the main-experiment length K=30.
# test90 = test + test_ext: held-out evaluation for trained methods (v2), same size as eval.
POOLS = {'eval': ['train', 'test'], 'dev': ['dev'], 'dev30': ['dev', 'dev_ext'], 'test90': ['test', 'test_ext'],
         'train': ['train']}
DEFAULT_KS = {'eval': [1, 10, 30, 90], 'dev': [1, 10], 'dev30': [30], 'test90': [30], 'train': [10, 20, 40]}
SEED = 20260923


def read_jsonl(path):
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))


def load_pool(lang, splits):
    scenarios, sessions, questions = [], [], []
    for sp in splits:
        d = ROOT / LANGS[lang] / sp
        scenarios += read_jsonl(d / 'scenarios.jsonl')
        sessions += read_jsonl(d / 'sessions.jsonl')
        questions += read_jsonl(d / 'questions.jsonl')
    return scenarios, sessions, questions


def stratified_groups(scenarios, k, seed):
    """Deal domain-shuffled scenarios round-robin into len//k groups of size k."""
    if len(scenarios) % k:
        raise ValueError(f'K={k} does not divide pool size {len(scenarios)}')
    rng = random.Random(seed)
    by_domain = defaultdict(list)
    for s in sorted(scenarios, key=lambda s: s['scenario_id']):
        by_domain[s['domain']].append(s['scenario_id'])
    order = []
    for dom in sorted(by_domain):
        ids = by_domain[dom][:]
        rng.shuffle(ids)
        order += ids
    n_groups = len(scenarios) // k
    groups = [[] for _ in range(n_groups)]
    for idx, sid in enumerate(order):
        groups[idx % n_groups].append(sid)
    return [sorted(g) for g in groups]


def validate_history(history, questions):
    errors = []
    dialog_ids = [t['dialog_id'] for s in history['sessions'] for t in s['turns']]
    if len(dialog_ids) != len(set(dialog_ids)):
        errors.append(f"{history['history_id']}: duplicate dialog_id")
    starts = [(s['session_start'], s['session_id']) for s in history['sessions']]
    if starts != sorted(starts):
        errors.append(f"{history['history_id']}: sessions not chronological")
    # Scenarios must not share entities, otherwise a distractor could legitimately answer.
    owner = {}
    for s in history['sessions']:
        for t in s['turns']:
            for m in t.get('mentions', []):
                if owner.setdefault(m, s['scenario_id']) != s['scenario_id']:
                    errors.append(f"{history['history_id']}: entity {m} shared across scenarios")
    ids = set(dialog_ids)
    for q in questions:
        missing = [e for e in q['evidence'] if e not in ids]
        if missing:
            errors.append(f"{q['question_id']}: evidence {missing[:3]} not in {history['history_id']}")
    return errors


def build(lang, pool, ks, seed=SEED, out_root=ROOT / 'long'):
    scenarios, sessions, questions = load_pool(lang, POOLS[pool])
    sessions_by_scen, questions_by_scen = defaultdict(list), defaultdict(list)
    for s in sessions:
        sessions_by_scen[s['scenario_id']].append(s)
    for q in questions:
        questions_by_scen[q['scenario_id']].append(q)
    errors = []
    for k in ks:
        histories, out_questions = [], []
        for g, members in enumerate(stratified_groups(scenarios, k, seed + k), 1):
            hid = f'H_{lang.upper()}_{pool.upper()}_K{k:03d}_{g:03d}'
            merged = sorted((s for sid in members for s in sessions_by_scen[sid]),
                            key=lambda s: (s['session_start'], s['session_id']))
            history = {'history_id': hid, 'lang': lang, 'pool': pool, 'K': k, 'scenario_ids': members,
                       'n_sessions': len(merged), 'n_turns': sum(len(s['turns']) for s in merged),
                       'sessions': merged}
            qs = [{**q, 'history_id': hid, 'K': k} for sid in members for q in questions_by_scen[sid]]
            errors += validate_history(history, qs)
            histories.append(history)
            out_questions += qs
        out = out_root / lang / pool / f'K{k:03d}'
        write_jsonl(out / 'histories.jsonl', histories)
        write_jsonl(out / 'questions.jsonl', out_questions)
        print(f'[{lang}/{pool}] K={k}: {len(histories)} histories, {len(out_questions)} questions -> {out}')
    return errors


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--langs', nargs='+', default=list(LANGS))
    p.add_argument('--eval-ks', nargs='+', type=int, default=DEFAULT_KS['eval'])
    p.add_argument('--dev-ks', nargs='+', type=int, default=DEFAULT_KS['dev'])
    p.add_argument('--dev30-ks', nargs='+', type=int, default=DEFAULT_KS['dev30'])
    p.add_argument('--test90-ks', nargs='+', type=int, default=DEFAULT_KS['test90'])
    p.add_argument('--train-ks', nargs='+', type=int, default=DEFAULT_KS['train'])
    p.add_argument('--pools', nargs='+', default=list(POOLS), choices=list(POOLS),
                   help='rebuild only these pools (the others are left untouched)')
    p.add_argument('--seed', type=int, default=SEED)
    args = p.parse_args()
    errors = []
    for lang in args.langs:
        for pool in args.pools:
            errors += build(lang, pool, getattr(args, f'{pool}_ks'), args.seed)
    print(json.dumps({'errors': errors[:50], 'n_errors': len(errors)}, ensure_ascii=False, indent=2))
    raise SystemExit(1 if errors else 0)
