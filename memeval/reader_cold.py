"""Cold-start data for ShopMem-Reader (方法论_v3_大纲 §3.4.3 step 1; 实验计划_v3 stage R1).

generate: luna plays the Reader (memeval.reader.run_episode) on train questions over a ShopMem-Writer
    bank -- n trajectories per question; the first --n-search of them get a hint appended to the
    system prompt ("search at least once before answering"), so trajectories with searches are not
    left to chance (the 8B prototype searched in ~7% of answers). The hint is not saved: the saved
    messages carry the plain Reader prompt, the one the trained Reader is served with. Each
    trajectory is judged by luna right away. luna samples are separated by the request seed (luna
    samples even at temperature 0, and the seed keeps their cache keys apart).
    Rows -> runs/<run-name>/trajectories.jsonl (appended, resumable).
judge: judge the rows of a run again with any judge model / endpoint (e.g. the sol rows generated with
    --no-judge, or a second judge for judge agreement). Verdicts -> runs/<run-name>/judge_<tag>.jsonl
    ({question_id, sample, correct, parse_ok, judge_raw, judge_model}); trajectories.jsonl is not touched.
    Resumable and safe to rerun while generation is still appending rows (only new rows are judged).
build: per question keep one judged-correct trajectory with a clean format (format_ok: every reply
    strictly one valid action as memeval.reader_format defines it, no forced answer). If the question has correct trajectories both with and without a
    search, a hash of the question id picks the searched one with probability --search-share,
    otherwise whichever kind exists (fewer searches, then lower sample first). Questions are split
    train / val by a hash of the question id. -> runs/<run-name>/sft/{train,val}.jsonl
    ({"messages": [...], question_id, category, searches, sample}) + stats.json. A question with no
    clean correct trajectory is dropped (no_kept, per category in stats.json: check it before training,
    a high share means far less SFT data than questions).
    --votes <luna k-vote file> --min-votes m: a trajectory counts as correct only with >= m CORRECT votes of the
    majority judge (scripts/judge_luna_majority.py). --drop-lapse-claims: constraint_recall answers that call a
    registered constraint lapsed are never kept (see LAPSE).

    python -m memeval.reader_cold generate --run-name reader_cold_K20 --bank-dir runs/topr1_train_K20/banks \
        --pool train --K 20 --n 4 --n-search 2
    python -m memeval.reader_cold judge --run-name reader_cold_K20 --judge-model gpt-6-luna --judge-endpoint yan-gpt
    python -m memeval.reader_cold build --run-name reader_cold_K20 --search-share 0.5
    python -m memeval.reader_cold build --run-name reader_cold_sol_K20_all_v3 --runs reader_cold_sol_K20 \
        reader_cold_sol_K20_micu reader_cold_sol_K20_yanredo --votes results/reader_cold_sol_luna5.jsonl \
        --min-votes 4 --drop-lapse-claims
"""
import argparse
import hashlib
import json
import random
import re
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .data import ROOT, load, read_jsonl
from .llm import UsageMeter
from .prompts import judge_messages
from .reader import bank_file, banked_writer, run_episode
from .reader_format import parse_action
from .score import parse_label
from .teacher import ENDPOINTS, LUNA, QWEN8B, RETRIEVAL, endpoint

HINT = ('\nFor this question, search the memory at least once before you answer: check the first retrieval '
        'against a targeted search (an ID, a shift, or an earlier / current state).')


FORMAT_HINT = ('\nYour whole reply must be that single JSON object: one object, nothing before or after it, '
               'no second object, no tool-call markup.')


class FormatRetry:
    """Teacher wrapper: a reply that is not strictly one valid action is re-sampled with another seed
    (up to `retries` times; the last reply is kept if none is valid). Rejection sampling per step, so a
    trajectory keeps only the teacher's own clean replies. Retry i>0 uses seed 1000*(i)+seed; the first
    call keeps the original params (cache hits of earlier runs stay valid)."""

    def __init__(self, llm, retries):
        self._llm, self.retries, self.resampled = llm, retries, 0

    def complete(self, messages, stage, **params):
        c = self._llm.complete(messages, stage, **params)
        for i in range(1, self.retries + 1):
            if parse_action(c.text) is not None:
                break
            self.resampled += 1
            c = self._llm.complete(messages, stage, **(params | {'seed': 1000 * i + params.get('seed', 0)}))
        return c


def _hash(s):
    return (int(hashlib.md5(s.encode()).hexdigest(), 16) % 10_000) / 10_000


def generate(a):
    out = Path(a.out) / a.run_name
    out.mkdir(parents=True, exist_ok=True)
    cfg = {'pool': a.pool, 'K': a.K, 'bank_dir': a.bank_dir, 'n': a.n, 'n_search': a.n_search, 't_max': a.t_max,
           'teacher': a.teacher_model, 'teacher_temperature': a.temperature, 'per_category': a.per_category,
           'method_config': RETRIEVAL | {'closed_view': 'rule'},
           'format_hint': a.format_hint, 'format_retries': a.format_retries,
           'teacher_endpoint': a.teacher_endpoint,
           'judge': not a.no_judge, 'exclude_run': a.exclude_run, 'keys': a.keys}
    (out / 'config.json').write_text(json.dumps(cfg, indent=2))
    histories, questions, _ = load('en', a.pool, a.K)
    if a.per_category:
        by_cat = defaultdict(list)
        for q in questions:
            by_cat[q.category].append(q)
        rng = random.Random(a.seed)
        questions = [q for c in sorted(by_cat) for q in rng.sample(by_cat[c], min(a.per_category, len(by_cat[c])))]
    if a.question_ids:
        ids = set(Path(a.question_ids).read_text().split())
        questions = [q for q in questions if q.question_id in ids]
        cfg['question_ids'] = a.question_ids
        (out / 'config.json').write_text(json.dumps(cfg, indent=2))
    meter, sem = UsageMeter(), threading.BoundedSemaphore(a.gpt_workers)
    luna = endpoint(ENDPOINTS[a.teacher_endpoint] | {'model': a.teacher_model}, a.cache, meter, sem, max_tokens=1024)
    if a.format_retries:
        luna = FormatRetry(luna, a.format_retries)
    fmt = FORMAT_HINT if a.format_hint else ''
    judge = endpoint(LUNA, a.cache, meter, sem, max_tokens=256)
    planner = endpoint(QWEN8B, a.cache, meter)  # the retrieval planner is the evaluation's 8B
    memories = {h.history_id: banked_writer(planner, h, bank_file(a.bank_dir, h.history_id), cfg['method_config'])
                for h in histories if any(q.history_id == h.history_id for q in questions)}
    path = out / 'trajectories.jsonl'
    done = {(r['question_id'], r['sample']) for r in read_jsonl(path)} if path.exists() else set()
    if a.exclude_run:  # questions already generated by another run of the same pool
        done |= {(r['question_id'], r['sample']) for r in read_jsonl(Path(a.out) / a.exclude_run / 'trajectories.jsonl')}
    jobs = [(q, k) for q in questions for k in range(a.n) if (q.question_id, k) not in done]
    if a.keys:  # only these (question, sample) pairs, e.g. rows quarantined from another run
        want = {(x.split()[0], int(x.split()[1])) for x in Path(a.keys).read_text().splitlines() if x.strip()}
        jobs = [(q, k) for q, k in jobs if (q.question_id, k) in want]
    print(f'{len(questions)} questions x {a.n} samples: {len(jobs)} trajectories to run', flush=True)
    lock = threading.Lock()

    def one(job):
        q, k = job
        hinted = k < a.n_search
        ep = run_episode(luna, memories[q.history_id], q, a.t_max, hint=(HINT if hinted else '') + fmt,
                         temperature=a.temperature, seed=k)
        v = None if a.no_judge else judge.complete(judge_messages(q.category, q.question, q.reference,
                                                                    ep['prediction']), stage='judge')
        row = {'question_id': q.question_id, 'history_id': q.history_id, 'category': q.category, 'sample': k,
               'hinted': hinted, 'correct': v and parse_label(v.text), 'judge_raw': v and v.text,
               'teacher_endpoint': a.teacher_endpoint} | ep
        with lock, open(path, 'a') as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + '\n')

    with ThreadPoolExecutor(a.workers) as pool:
        for i, _ in enumerate(pool.map(one, jobs)):
            if i % 200 == 0:
                print(i, json.dumps(meter.snapshot()), flush=True)
    usage = out / 'usage.json'
    prev = json.loads(usage.read_text()) if usage.exists() else {}
    snap = meter.snapshot()
    for stage, vals in prev.items():
        for key, v in vals.items():
            snap.setdefault(stage, {})[key] = snap.get(stage, {}).get(key, 0) + v
    usage.write_text(json.dumps(snap, indent=2))
    print('DONE', json.dumps(meter.snapshot()), 'resampled', getattr(luna, 'resampled', 0), flush=True)


def judge_run(a):
    out = Path(a.out) / a.run_name
    tag = a.tag or a.judge_model
    path = out / f'judge_{tag}.jsonl'
    done = {(r['question_id'], r['sample']) for r in read_jsonl(path)} if path.exists() else set()
    rows = [r for r in read_jsonl(out / 'trajectories.jsonl') if (r['question_id'], r['sample']) not in done]
    _, questions, _ = load('en', a.pool, a.K)
    qs = {q.question_id: q for q in questions}
    meter = UsageMeter()
    judge = endpoint(ENDPOINTS[a.judge_endpoint] | {'model': a.judge_model}, a.cache, meter, max_tokens=a.max_tokens)
    print(f'{len(rows)} rows to judge with {a.judge_model} on {a.judge_endpoint} -> {path}', flush=True)
    lock = threading.Lock()

    def one(r):
        q = qs[r['question_id']]
        v = judge.complete(judge_messages(q.category, q.question, q.reference, r['prediction']), stage='judge')
        row = {'question_id': r['question_id'], 'sample': r['sample'], 'correct': parse_label(v.text),
               'parse_ok': 'CORRECT' in v.text.upper() or 'WRONG' in v.text.upper(), 'judge_raw': v.text,
               'judge_model': a.judge_model}
        with lock, open(path, 'a') as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + '\n')

    with ThreadPoolExecutor(a.workers) as pool:
        for i, _ in enumerate(pool.map(one, rows)):
            if i % 500 == 0:
                print(i, json.dumps(meter.snapshot()), flush=True)
    print('DONE', json.dumps(meter.snapshot()), flush=True)


def merged_rows(out_dir, run_names, judge_tag, overrides=None):
    """Trajectories of several runs of one pool, with the verdict of `judge_tag` (judge_<tag>.jsonl) where a
    run has no inline verdict (generated with --no-judge). A (question, sample) pair must come from exactly
    one run and every row must carry a verdict. Also attaches the other judge files of each run as
    row['verdicts'][tag] (e.g. the sol judge, for analysis; not used to select). `overrides`: a JSON list of
    {run, question_id, sample, correct} that replaces the selecting verdict (e.g. a majority re-judge)."""
    over = {(o['run'], o['question_id'], o['sample']): o['correct'] for o in json.loads(Path(overrides).read_text())} \
        if overrides else {}
    rows, seen = [], {}
    for name in run_names:
        d = Path(out_dir) / name
        verdicts = {}
        for f in sorted(d.glob('judge_*.jsonl')):
            if '.quarantine' in f.name or '.replaced' in f.name:
                continue
            tag = f.stem[len('judge_'):]
            verdicts[tag] = {(v['question_id'], v['sample']): v['correct'] for v in read_jsonl(f)}
        for r in read_jsonl(d / 'trajectories.jsonl'):
            k = (r['question_id'], r['sample'])
            if k in seen:
                raise ValueError(f'{k} in both {seen[k]} and {name}')
            seen[k] = name
            r['run'] = name
            r['verdicts'] = {tag: v[k] for tag, v in verdicts.items() if k in v}
            if r['correct'] is None:
                if judge_tag not in r['verdicts']:
                    raise ValueError(f'{k} of {name}: no inline verdict and no judge_{judge_tag}.jsonl verdict')
                r['correct'] = r['verdicts'][judge_tag]
            if (name,) + k in over:
                r['verdict_override'] = {'from': r['correct'], 'to': over[(name,) + k]}
                r['correct'] = over[(name,) + k]
            rows.append(r)
    missing = set(over) - {(r['run'], r['question_id'], r['sample']) for r in rows}
    if missing:
        raise ValueError(f'overrides for rows not in the runs: {sorted(missing)[:3]}')
    return rows


def pred_hash(p):
    return hashlib.sha256(p.encode()).hexdigest()[:16]


def apply_votes(rows, path, min_votes):
    """Replace each row's selecting verdict by the luna k-vote majority (scripts/judge_luna_majority.py, keyed
    by (question_id, pred_hash)): correct iff at least `min_votes` votes say CORRECT. Every row must have a
    vote record and every raw vote must carry an explicit CORRECT/WRONG label (parse_label reads anything
    else as WRONG, which must not pass silently)."""
    votes = {}
    for v in read_jsonl(path):
        for raw in v['raws']:
            try:
                label = json.loads(raw[raw.index('{'): raw.rindex('}') + 1]).get('label', '')
            except (ValueError, json.JSONDecodeError):
                label = ''
            if str(label).upper() not in ('CORRECT', 'WRONG'):
                raise ValueError(f'unlabelled vote for {v["question_id"]} {v["pred_hash"]}: {raw[:120]!r}')
        votes[(v['question_id'], v['pred_hash'])] = v
    for r in rows:
        v = votes.get((r['question_id'], pred_hash(r['prediction'])))
        if v is None:
            raise ValueError(f'no vote record for {r["question_id"]} sample {r["sample"]} ({r["run"]})')
        r['n_votes'], r['votes_correct'] = len(v['votes']), v['n_correct']
        r['verdict_votes'] = {'from': r['correct'], 'to': v['n_correct'] >= min_votes}
        r['correct'] = v['n_correct'] >= min_votes


# constraint_recall answers that call a registered constraint lapsed ("no longer valid since ...",
# "valid until ...", "cleared", ...). The bank marks a constraint unit closed once its task is closed and
# the teacher copies that mark into the answer, but the dialogue never lifts the constraints and one of
# them ("must not reuse an adjustment that has already failed") is standing; such answers split the judge.
# A lapse sentence that names only the closure restriction ("the closure restriction was lifted", "cleared for
# closure") is true and is not a lapse claim; one about the constraints as a whole, the record or the reuse
# ban is.
LAPSE = re.compile(r'(ceas|no longer|lifted|expired|stopped (being|applying)|became invalid|longer valid|valid until|'
                   r'released|\bcleared\b|\bended\b|ending the|superseded|(became|was|were) satisfied|'
                   r'constraint .{0,30}satisfied)', re.I)
CLOSURE_ONLY = re.compile(r'\b(the|its|this) closure (restriction|constraint|condition|block)\b|\bcleared for closure\b',
                          re.I)
NOT_CLOSURE_ONLY = re.compile(r'\b(these|both|all|constraints|record|reus)', re.I)


def lapse_claim(r):
    if r['category'] != 'constraint_recall':
        return False
    return any(LAPSE.search(s) and not (CLOSURE_ONLY.search(s) and not NOT_CLOSURE_ONLY.search(s))
               for s in re.split(r'(?<=[.;])\s+|\n+', r['prediction']))


def check_rows(rows, n):
    """Hard checks before anything is kept: n samples per question, the saved system prompt carries no
    generation-time hint, and every saved reply's format flag agrees with the strict parser."""
    per_q = Counter(r['question_id'] for r in rows)
    bad = {q: c for q, c in per_q.items() if c != n}
    if bad:
        raise ValueError(f'{len(bad)} questions without {n} samples, e.g. {list(bad.items())[:3]}')
    for r in rows:
        sys_prompt = r['messages'][0]['content']
        if HINT.strip() in sys_prompt or FORMAT_HINT.strip() in sys_prompt:
            raise ValueError(f'hint text saved in the system prompt of {r["question_id"]} {r["sample"]}')
        strict = all(parse_action(m['content']) is not None for m in r['messages'] if m['role'] == 'assistant')
        if r['format_ok'] and not strict:
            raise ValueError(f'format_ok but a non-strict reply: {r["question_id"]} {r["sample"]}')


def build(a):
    out = Path(a.out) / a.run_name
    runs = a.runs or [a.run_name]
    if a.votes and a.overrides:
        raise ValueError('--votes already covers the re-judged rows; do not stack --overrides on it')
    rows = merged_rows(a.out, runs, a.judge, a.overrides)
    check_rows(rows, a.n)
    if a.votes:
        apply_votes(rows, a.votes, a.min_votes)
    by_q = defaultdict(list)
    for r in rows:
        by_q[r['question_id']].append(r)
    splits, stats = {'train': [], 'val': []}, Counter()
    per_cat = defaultdict(Counter)
    for qid, rs in sorted(by_q.items()):
        cat = rs[0]['category']
        ok = [r for r in rs if r['correct'] and r['format_ok']]
        if a.drop_lapse_claims:
            stats['dropped_lapse_claim'] += sum(lapse_claim(r) for r in ok)
            ok = [r for r in ok if not lapse_claim(r)]
        per_cat[cat]['questions'] += 1
        stats['trajectories'] += len(rs)
        stats['correct'] += sum(r['correct'] for r in rs)
        stats['correct_clean'] += len(ok)
        stats['correct_not_clean'] += sum(r['correct'] and not r['format_ok'] for r in rs)
        stats['lenient_replies'] += sum(r['lenient_replies'] for r in rs)
        if not ok:
            stats['no_kept'] += 1
            per_cat[cat]['no_kept'] += 1
            continue
        searched = sorted((r for r in ok if r['searches']), key=lambda r: (len(r['searches']), r['sample']))
        plain = sorted((r for r in ok if not r['searches']), key=lambda r: r['sample'])
        if searched and plain:
            kept = searched[0] if _hash('search:' + qid) < a.search_share else plain[0]
        else:
            kept = (searched or plain)[0]
        split = 'val' if _hash(qid) < a.val_share else 'train'
        splits[split].append({'messages': kept['messages'], 'question_id': qid, 'history_id': kept['history_id'],
                              'category': cat, 'searches': len(kept['searches']), 'sample': kept['sample'],
                              'hinted': kept['hinted'], 'run': kept['run'],
                              'teacher_endpoint': kept.get('teacher_endpoint'), 'verdicts': kept['verdicts'],
                              'votes_correct': kept.get('votes_correct'), 'n_votes': kept.get('n_votes')})
        stats[f'kept_from_{kept["run"]}'] += 1
        stats[f'kept_{split}'] += 1
        stats['kept_with_search'] += bool(kept['searches'])
        per_cat[cat]['kept'] += 1
        per_cat[cat]['kept_with_search'] += bool(kept['searches'])
    sft = out / 'sft'
    sft.mkdir(parents=True, exist_ok=True)
    for name, rs in splits.items():
        (sft / f'{name}.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rs))
    kept = stats['kept_train'] + stats['kept_val']
    summary = {'runs': runs, 'judge': a.judge, 'overrides': a.overrides, 'votes': a.votes, 'min_votes': a.min_votes,
               'drop_lapse_claims': a.drop_lapse_claims,
               'verdicts_overridden': sum('verdict_override' in r for r in rows),
               'verdicts_flipped_by_votes': dict(Counter(f"{r['verdict_votes']['from']}->{r['verdict_votes']['to']}"
                                                         for r in rows if 'verdict_votes' in r
                                                         and r['verdict_votes']['from'] != r['verdict_votes']['to'])),
               'questions': len(by_q), **stats,
               'no_kept_share': stats['no_kept'] / max(len(by_q), 1),
               'kept_search_share': stats['kept_with_search'] / max(kept, 1),
               'search_share_target': a.search_share, 'val_share': a.val_share,
               'by_category': {c: dict(v) for c, v in sorted(per_cat.items())}}
    (sft / 'stats.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: v for k, v in summary.items() if k != 'by_category'}, indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    g = sub.add_parser('generate')
    g.add_argument('--bank-dir', required=True, help='bank files of a ShopMem-Writer run (<history_id>.jsonl)')
    g.add_argument('--pool', default='train')
    g.add_argument('--K', type=int, default=20)
    g.add_argument('--per-category', type=int, default=0, help='questions per category (0: all)')
    g.add_argument('--n', type=int, default=4, help='trajectories per question')
    g.add_argument('--n-search', type=int, default=2, help='of them, how many get the search-at-least-once hint')
    g.add_argument('--t-max', type=int, default=2)
    g.add_argument('--temperature', type=float, default=1.0)
    g.add_argument('--workers', type=int, default=16, help='trajectories in parallel (luna calls are capped below)')
    g.add_argument('--gpt-workers', type=int, default=8, help='concurrent luna requests (teacher + judge)')
    g.add_argument('--seed', type=int, default=0)
    g.add_argument('--teacher-model', default=LUNA['model'],
                   help='Reader teacher model (e.g. gpt-6.1-sol); the judge is always luna')
    g.add_argument('--format-hint', action='store_true', help='append FORMAT_HINT to the teacher prompt (not saved)')
    g.add_argument('--format-retries', type=int, default=0, help='re-sample a non-strict teacher reply up to k times')
    g.add_argument('--teacher-endpoint', default='yan-gpt', choices=sorted(ENDPOINTS),
                   help='GPT endpoint of the teacher (memeval.teacher.ENDPOINTS); the luna judge is always on yan-gpt')
    g.add_argument('--no-judge', action='store_true', help='skip the luna judge (correct=None; judge later)')
    g.add_argument('--exclude-run', default='', help='skip (question, sample) pairs already in this run')
    g.add_argument('--keys', default='', help='file of "question_id sample" lines: generate only these pairs')
    g.add_argument('--question-ids', default='', help='file of question ids (whitespace separated) to restrict to')
    b = sub.add_parser('build')
    b.add_argument('--search-share', type=float, default=0.5,
                   help='when a question has correct trajectories with and without a search: P(keep a searched one)')
    b.add_argument('--val-share', type=float, default=0.05)
    b.add_argument('--runs', nargs='+', default=[],
                   help='merge these runs (disjoint question/sample pairs); output still goes to --run-name')
    b.add_argument('--judge', default='gpt-6-luna',
                   help='verdicts for rows without an inline one: judge_<this>.jsonl of their run')
    b.add_argument('--n', type=int, default=4, help='samples every question must have')
    b.add_argument('--overrides', default='', help='JSON list of {run, question_id, sample, correct} verdict overrides')
    b.add_argument('--votes', default='', help='k-vote luna majority file (scripts/judge_luna_majority.py); replaces '
                                               'every selecting verdict')
    b.add_argument('--min-votes', type=int, default=4, help='with --votes: CORRECT votes needed to count as correct')
    b.add_argument('--drop-lapse-claims', action='store_true',
                   help='never keep a constraint_recall answer that calls a registered constraint lapsed')
    j = sub.add_parser('judge')
    j.add_argument('--judge-model', default=LUNA['model'])
    j.add_argument('--judge-endpoint', default='yan-gpt', choices=sorted(ENDPOINTS))
    j.add_argument('--tag', default='', help='output judge_<tag>.jsonl (default: the judge model)')
    j.add_argument('--pool', default='train')
    j.add_argument('--K', type=int, default=20)
    j.add_argument('--workers', type=int, default=8)
    j.add_argument('--max-tokens', type=int, default=256)
    for s in (g, b, j):
        s.add_argument('--run-name', required=True)
        s.add_argument('--out', default=str(ROOT / 'runs'))
        s.add_argument('--cache', default=str(ROOT / '.cache' / 'llm.sqlite'))
    a = p.parse_args()
    {'generate': generate, 'judge': judge_run, 'build': build}[a.cmd](a)


if __name__ == '__main__':
    main()
