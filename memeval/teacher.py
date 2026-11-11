"""Teacher write traces and their window rewards for ShopMem-Writer SFT / TOPR (v2 phase 1, step 6).

generate: a teacher model (default gpt-6-luna) writes each history window by window. At
window j every sample k = 0..n starts from the same bank -- the one the backbone (sample 0)
has written up to j -- in the style of Memory-R2's local rollouts from shared memory states;
then the backbone's write is committed and the history moves on. Samples k > 0 are
independent draws (the request seed keeps them apart in the disk cache; luna samples even
at temperature 0). For TOPR rounds the trained Writer writes instead (--writer-model on a local
vLLM): the backbone greedily, samples 1..n at --temperature, plus --teacher-samples more by luna
(rows carry policy writer|teacher). Outputs, under runs/<run>/:
  samples/<history_id>.jsonl   every sample of every window: prompts, replies, facts, actions, r_ind
  banks/<history_id>.jsonl     the backbone's final bank; each unit carries the windows that wrote
                               and closed it, so the bank before any window can be rebuilt
Rerunning replays from the LLM disk cache, so an interrupted run resumes cheaply.

score: rewards of sampled writes, one row per (window, sample, answerer) in rewards.jsonl:
  r_evid   evidence turns first seen in the window covered by the memory after the write (revid)
  r_task   the questions attached to the window (evidence_map) retrieved from the bank before the
           window plus the sample's write (v1 retrieval, planned by Qwen3-8B as in evaluation),
           answered by the chosen answerer, judged per item by
           gpt-6-luna; share correct
  r_ind    copied from the sample
Answerers: qwen8b (local, the evaluation answerer) or luna -- the phase-1 pilot compares the two
on the same writes. Resumable (rows already in rewards.jsonl are skipped).

    python -m memeval.teacher generate --pool train --K 20 --n 4 --run-name teacher_train_K20
    python -m memeval.teacher generate --pool train --K 20 --n 8 --temperature 1.0 --teacher-samples 1 \
        --writer-model writer_ep3 --writer-base-url http://localhost:18010/v1 --run-name topr1_train_K20 \
        --method-config closed_view=rule
    python -m memeval.teacher score --run-name teacher_train_K20 --answerer qwen8b --limit-windows 40
"""
import argparse
import json
import random
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .data import ROOT, load, read_jsonl
from .evidence_map import window_map
from .llm import UsageMeter, count_tokens, make_llm
from .memory_ops import Action, Fact, MemoryBank, Unit
from .methods.shopmem_writer import ShopMemWriter, write_row
from .prompts import answer_messages, judge_messages, parse_answer
from .revid import coverage, covered_turns
from .run import parse_kv
from .score import parse_label

# GPT endpoints by name. yan-gpt: the main one (luna + sol, chat completions; luna judge always runs here).
# micu-gpt: a relay with sol only; its chat route prepends a Codex system prompt, so it is called through
# the Responses API, where our system prompt goes in `instructions` and replaces it.
ENDPOINTS = {
    'yan-gpt': {'base_url': 'https://api.openai.com/v1', 'api_key_env': 'JUDGE_API_KEY', 'api': 'chat'},
    'micu-gpt': {'base_url': 'https://api.openai.com/v1', 'api_key_env': 'MICU_API_KEY', 'api': 'responses'},
}
LUNA = {'model': 'gpt-6-luna'} | ENDPOINTS['yan-gpt']
QWEN8B = {'model': 'Qwen3-8B', 'base_url': 'http://localhost:8001/v1', 'api_key_env': '',
          'extra_body': {'chat_template_kwargs': {'enable_thinking': False}}}
RETRIEVAL = {'embed_base_url': 'http://localhost:8006/v1', 'embed_model': 'Qwen3-Embedding-8B',
             'rerank_base_url': 'http://localhost:8007/v1', 'rerank_model': 'Qwen3-Reranker-4B'}


class Throttled:
    """An LLM whose complete() calls share one concurrency cap (external API limit)."""

    def __init__(self, llm, sem):
        self._llm, self._sem = llm, sem

    def complete(self, *args, **kw):
        with self._sem:
            return self._llm.complete(*args, **kw)

    def __getattr__(self, name):
        return getattr(self._llm, name)


def endpoint(spec, cache, meter, sem=None, max_tokens=1024):
    llm = make_llm(spec['model'], spec['base_url'], spec['api_key_env'], cache_path=cache, meter=meter,
                   max_tokens=max_tokens, extra_body=spec.get('extra_body'), api=spec.get('api', 'chat'))
    return Throttled(llm, sem) if sem else llm


# ---------------------------------------------------------------- generate
def generate_history(h, cfg, llm, n, out, max_windows=None, teacher=None, n_teacher=0):
    w_ = ShopMemWriter(llm, 'en', **cfg)
    windows, known = w_.setup(h)
    w_.teacher, w_.teacher_samples = teacher, set(range(n + 1, n + 1 + n_teacher))
    ks = range(n + 1 + n_teacher)
    windows = windows[:max_windows] if max_windows else windows
    with ThreadPoolExecutor(32) as pool:  # extraction does not depend on the bank: all at once
        ext = {(w.index, k): f for (w, k), f in zip(
            [(w, k) for w in windows for k in ks],
            pool.map(lambda wk: w_.extract(wk[0], known[wk[0].index], sample=wk[1]),
                     [(w, k) for w in windows for k in ks]))}
    path = out / 'samples' / f'{h.history_id}.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w') as fh, ThreadPoolExecutor(len(ks)) as pool:
        for w in windows:
            writes = list(pool.map(lambda k: w_.propose(w, known[w.index], ext[(w.index, k)], sample=k), ks))
            for wr in writes:
                fh.write(json.dumps(write_row(wr), ensure_ascii=False) + '\n')
            fh.flush()
            w_.commit(w, writes[0])
    w_.config['bank_path'] = str(out / 'banks')
    w_.finish()
    s = w_.memory_stats()
    print(f'{h.history_id}: {len(windows)} windows x {len(ks)} samples, backbone r_ind all-pass {s["rind_all_pass"]:.2f}')
    return s


def generate(a):
    out = Path(a.out) / a.run_name
    out.mkdir(parents=True, exist_ok=True)
    cfg = RETRIEVAL | parse_kv(a.method_config)
    histories, _, _ = load('en', a.pool, a.K)
    histories = histories[:a.max_histories] if a.max_histories else histories
    writer = LUNA if not a.writer_model else {'model': a.writer_model, 'base_url': a.writer_base_url, 'api_key_env': '',
                                              'extra_body': QWEN8B['extra_body']}
    cfg |= {'sample_temperature': a.temperature} if a.temperature is not None else {}
    (out / 'config.json').write_text(json.dumps({'lang': 'en', 'pool': a.pool, 'K': a.K, 'n': a.n, 'writer': writer,
                                                 'teacher': LUNA, 'teacher_samples': a.teacher_samples,
                                                 'method_config': cfg, 'max_windows': a.max_windows}, indent=2))
    meter, sem = UsageMeter(), threading.BoundedSemaphore(a.gpt_workers)
    luna = endpoint(LUNA, a.cache, meter, sem, max_tokens=2048)
    llm = luna if writer is LUNA else endpoint(writer, a.cache, meter, max_tokens=2048)
    with ThreadPoolExecutor(len(histories)) as pool:
        stats = list(pool.map(lambda h: generate_history(h, cfg, llm, a.n, out, a.max_windows, luna, a.teacher_samples),
                              histories))
    (out / 'generate_stats.json').write_text(json.dumps({'histories': {h.history_id: s for h, s in zip(histories, stats)},
                                                         'usage': meter.snapshot()}, indent=2))


# ---------------------------------------------------------------- score
def _write_from_row(row):
    facts = [Fact(**f) for f in row['facts']]
    actions = [Action(**x) for x in row['actions']]
    return {'facts': facts, 'actions': actions, 'manage': {'related': row['manage']['related']}}


def score(a):
    out = Path(a.out) / a.run_name
    cfg_run = json.loads((out / 'config.json').read_text())
    cfg = cfg_run['method_config']
    histories, questions, gold = load('en', cfg_run['pool'], cfg_run['K'])
    by_q = {q.question_id: q for q in questions}
    meter, sem = UsageMeter(), threading.BoundedSemaphore(a.gpt_workers)
    judge = endpoint(LUNA, a.cache, meter, sem, max_tokens=256)
    qwen = endpoint(QWEN8B, a.cache, meter)  # the retrieval planner is the evaluation's 8B whoever answers
    answerer = endpoint(LUNA, a.cache, meter, sem) if a.answerer == 'luna' else qwen
    path = out / 'rewards.jsonl'
    done = {(r['history_id'], r['window'], r['sample'], r['answerer']) for r in read_jsonl(path)} if path.exists() else set()
    lock = threading.Lock()

    # which (history, window) rows to score: every window with evidence or questions, or a random subset
    targets = []
    maps = {}
    for h in histories:
        f = out / 'samples' / f'{h.history_id}.jsonl'
        if not f.exists():
            continue
        rows, _ = window_map(h, questions, gold.low_density[h.history_id], int(cfg.get('window_size', 16)),
                             int(cfg.get('overlap', 2)))
        maps[h.history_id] = {r['window']: r for r in rows}
        wins = sorted({r['window'] for r in read_jsonl(f)})
        targets += [(h, j) for j in wins if maps[h.history_id][j]['questions'] or
                    (not a.qa_only and maps[h.history_id][j]['evidence_turns'])]
    scored_by = {}
    if path.exists():
        for r in read_jsonl(path):
            scored_by.setdefault(r['answerer'], set()).add((r['history_id'], r['window'], r['sample']))
    if a.match:  # only the writes another answerer already scored (paired comparison on a partial run)
        keep = scored_by.get(a.match, set())
        targets = [t for t in targets if any(k[:2] == (t[0].history_id, t[1]) for k in keep)]
    elif a.limit_windows:
        targets = [t for t in targets if maps[t[0].history_id][t[1]]['questions']]
        random.Random(a.seed).shuffle(targets)
        targets = sorted(targets[:a.limit_windows], key=lambda t: (t[0].history_id, t[1]))

    backbones, jobs = {}, []
    for h in {t[0].history_id: t[0] for t in targets}.values():
        samples = {}
        for r in read_jsonl(out / 'samples' / f'{h.history_id}.jsonl'):
            samples.setdefault(r['window'], []).append(r)
        backbones[h.history_id] = [Unit.from_dict(d) for d in read_jsonl(out / 'banks' / f'{h.history_id}.jsonl')]
        jobs += [(h, j, row) for _, j in [t for t in targets if t[0].history_id == h.history_id]
                 for row in samples[j] if (h.history_id, j, row['sample'], a.answerer) not in done
                 and (not a.match or (h.history_id, j, row['sample']) in scored_by.get(a.match, set()))]

    def score_row(job):
        """One sampled write: its own Writer state (bank before window j + the write), retrieval, answers, verdicts."""
        h, j, row = job
        m = maps[h.history_id][j]
        qs = [by_q[x] for x in m['questions']]
        w_ = ShopMemWriter(qwen, 'en', **cfg)
        windows, _ = w_.setup(h)
        bank = MemoryBank.before(backbones[h.history_id], j)
        w_.apply_write(bank, windows[j], _write_from_row(row))
        cov = covered_turns(bank.units)
        result = {'history_id': h.history_id, 'window': j, 'sample': row['sample'], 'answerer': a.answerer,
                  'role': m['role'], 'r_ind': row['rind']['score'], 'r_ind_all_pass': row['rind']['all_pass'],
                  'r_evid': coverage(m['evidence_turns'], cov), 'questions': []}
        if qs:
            w_.bank, w_._views = bank, {}

            def one(q):
                r = w_.retrieve(q.query())
                c = answerer.complete(answer_messages('en', r.context, q.question, q.as_of, w_.calendar_text, True),
                                      stage='answer', max_tokens=1024)
                pred = parse_answer(c.text)
                v = judge.complete(judge_messages(q.category, q.question, q.reference, pred), stage='judge')
                return {'question_id': q.question_id, 'category': q.category, 'prediction': pred,
                        'correct': parse_label(v.text), 'evidence_in_memory': coverage(q.evidence, cov),
                        'evidence_retrieved': coverage(q.evidence, set(r.provenance)),
                        'context_tokens': count_tokens(r.context)}
            with ThreadPoolExecutor(a.workers) as pool:
                result['questions'] = list(pool.map(one, qs))
            result['r_task'] = sum(x['correct'] for x in result['questions']) / len(qs)
        with lock, open(path, 'a') as fh:
            fh.write(json.dumps(result, ensure_ascii=False) + '\n')

    with ThreadPoolExecutor(a.row_workers) as pool:
        list(pool.map(score_row, jobs))
    print(f'scored {len(jobs)} sampled writes ({a.answerer})')
    usage = out / f'score_usage_{a.answerer}.json'
    prev = json.loads(usage.read_text()) if usage.exists() else {}
    snap = meter.snapshot()
    for stage, vals in prev.items():
        for k, v in vals.items():
            snap.setdefault(stage, {})[k] = snap.get(stage, {}).get(k, 0) + v
    usage.write_text(json.dumps(snap, indent=2))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    g = sub.add_parser('generate')
    g.add_argument('--pool', default='train')
    g.add_argument('--K', type=int, default=20)
    g.add_argument('--n', type=int, default=4, help='samples per window besides the backbone')
    g.add_argument('--max-histories', type=int, default=0)
    g.add_argument('--max-windows', type=int, default=0, help='only the first windows of each history (smoke)')
    g.add_argument('--writer-model', help='a local vLLM Writer (e.g. writer_ep3) instead of luna writing')
    g.add_argument('--writer-base-url', default='http://localhost:18010/v1')
    g.add_argument('--temperature', type=float, help='sampling temperature of samples k > 0 (default: the writer\'s)')
    g.add_argument('--teacher-samples', type=int, default=0, help='extra samples per window written by luna')
    g.add_argument('--method-config', nargs='*', metavar='KEY=VALUE')
    s = sub.add_parser('score')
    s.add_argument('--answerer', choices=['qwen8b', 'luna'], required=True)
    s.add_argument('--limit-windows', type=int, default=0, help='a random subset of question windows (pilot)')
    s.add_argument('--qa-only', action='store_true', help='skip windows with evidence but no attached question')
    s.add_argument('--seed', type=int, default=0)
    s.add_argument('--match', choices=['qwen8b', 'luna'], help='score only the writes this answerer has scored')
    s.add_argument('--workers', type=int, default=4, help='questions of one sampled write answered in parallel')
    s.add_argument('--row-workers', type=int, default=4, help='sampled writes scored in parallel')
    for x in (g, s):
        x.add_argument('--run-name', required=True)
        x.add_argument('--gpt-workers', type=int, default=8, help='cap on concurrent gpt-6-luna requests')
        x.add_argument('--cache', default=str(ROOT / '.cache' / 'llm.sqlite'))
        x.add_argument('--out', default=str(ROOT / 'runs'))
    a = p.parse_args()
    (generate if a.cmd == 'generate' else score)(a)


if __name__ == '__main__':
    main()
