"""Rollouts and rewards for ShopMem-Reader GRPO (方法论_v3_大纲 §3.4.3 step 2; 实验计划_v3 stage R2).

The Reader policy is served by vLLM (on autoDL-B, reached through an SSH tunnel; every adapter the
trainer saves is loaded there as a LoRA under its own name). Episodes run here on lab, because the
search tool is the v1 retrieval over the Writer's bank (Qwen3-8B planner, embedding, reranker on lab)
and the judge is luna. Two commands:

pool: pick the GRPO questions. The SFT Reader answers every train question G times (T=1, t_max as in
    training), luna judges each answer; rows -> runs/<run-name>/pool_rollouts.jsonl (appended,
    resumable), then pool.json lists the questions whose G answers are not all correct and not all wrong
    (with all-same answers the group advantage is 0, so they give no gradient), and stats per category.
serve: an HTTP endpoint for the trainer. POST /rollout {"adapter": <served LoRA name>, "question_ids": [...],
    "G": 16, "seed": <step>} runs G episodes per question with that adapter and returns, per episode,
    {question_id, i, messages, prediction, searches, format_ok, correct, reward, r_task, r_fmt, r_len,
    turns} (rewards below). turns: per Reader reply, the exact prompt token ids vLLM rendered, the
    sampled token ids and vLLM's log-probabilities of them, so the trainer scores the very tokens that
    were sampled (no re-tokenisation) and can mask tokens where its own log-probabilities disagree with
    vLLM's (training-inference mismatch). The trainer computes the group advantages and the loss; this
    side only plays the episodes. Every rollout (without turns) is appended to runs/<run-name>/rollouts.jsonl.

Reward (§3.4.3): r = r_task + λ_f · r_fmt + r_len, r_task = luna verdict (1 / 0), r_fmt = format_ok (every
reply strictly one valid action, no search past the budget, ended on an answer: memeval.reader.run_episode),
and r_fmt = 0 when the answer is wrong, so format alone earns nothing. r_len: a length penalty on the
answer text (the "answer" field), 0 up to --len-start tokens, falling linearly to −--len-penalty at
--len-end and beyond (the luna judge may favour long answers; the cold-start answers have p99 406 tokens).

Episode sampling: temperature 1, a per-episode seed (step * 1000 + i) so the G samples of a group differ
and a rerun of the same step replays from the disk cache. The Reader is always sent with thinking off.

    python -m memeval.reader_rl pool --run-name reader_grpo_pool --reader-model reader_sft \
        --reader-base-url http://localhost:18100/v1 --G 8
    python -m memeval.reader_rl serve --run-name reader_grpo1 --reader-base-url http://localhost:18100/v1 \
        --port 18120
"""
import argparse
import json
import sys
import threading
import time
import traceback
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .data import ROOT, load, read_jsonl
from .llm import Completion, UsageMeter, count_tokens
from .prompts import judge_messages
from .reader import bank_file, banked_writer, run_episode
from .score import parse_label
from .teacher import LUNA, QWEN8B, RETRIEVAL, endpoint

NOTHINK = {'chat_template_kwargs': {'enable_thinking': False}}


def reward(correct, format_ok, answer_tokens, lambda_f, len_start=512, len_end=1024, len_penalty=0.5):
    """(r, r_task, r_fmt, r_len): r_fmt counts only for a correct answer; r_len penalises long answers."""
    r_task = 1.0 if correct else 0.0
    r_fmt = 1.0 if (correct and format_ok) else 0.0
    r_len = -len_penalty * min(max(answer_tokens - len_start, 0) / (len_end - len_start), 1.0)
    return r_task + lambda_f * r_fmt + r_len, r_task, r_fmt, r_len


class TokenReader:
    """The served Reader for training rollouts, one per episode: plain chat completions (no cache), and
    per reply the prompt token ids vLLM rendered, the sampled token ids and their log-probabilities
    (vLLM's return_token_ids / logprobs) in self.turns."""

    def __init__(self, client, model, max_tokens, meter, retries=5):
        self.client, self.model, self.max_tokens, self.meter, self.retries = client, model, max_tokens, meter, retries
        self.turns = []

    def complete(self, messages, stage, **params):
        for attempt in range(self.retries):
            try:
                t0 = time.perf_counter()
                r = self.client.chat.completions.create(
                    model=self.model, messages=messages, max_tokens=self.max_tokens, logprobs=True,
                    temperature=params.get('temperature', 1.0), seed=params.get('seed'),
                    extra_body={'return_token_ids': True, **NOTHINK})
                break
            except Exception:
                if attempt == self.retries - 1:
                    raise
                time.sleep(2 ** attempt)
        ch = r.choices[0]
        ids, lps = getattr(ch, 'token_ids', None), [x.logprob for x in ch.logprobs.content]
        prompt_ids = getattr(r, 'prompt_token_ids', None)
        if not ids or not prompt_ids or len(ids) != len(lps):
            raise RuntimeError('vLLM returned no token ids / logprobs (needs return_token_ids support)')
        self.turns.append({'prompt_ids': prompt_ids, 'ids': ids, 'logprobs': lps, 'finish': ch.finish_reason})
        c = Completion(ch.message.content or '', r.usage.prompt_tokens, r.usage.completion_tokens,
                       time.perf_counter() - t0, False)
        self.meter.add(stage, c)
        return c


class Env:
    """Questions, banks and models of one pool; play(q, reader, seed) is one judged episode."""

    def __init__(self, a, meter):
        self.a = a
        histories, questions, _ = load('en', a.pool, a.K)
        self.questions = {q.question_id: q for q in questions}
        sem = threading.BoundedSemaphore(a.gpt_workers)
        self.judge = endpoint(LUNA, a.cache, meter, sem, max_tokens=256)
        planner = endpoint(QWEN8B, a.cache, meter)  # the retrieval planner is the evaluation's 8B
        cfg = RETRIEVAL | {'closed_view': 'rule'}
        self.memories = {h.history_id: banked_writer(planner, h, bank_file(a.bank_dir, h.history_id), cfg)
                         for h in histories}
        for m in self.memories.values():
            m.finish()
        self.meter = meter

    def reader(self, model):
        """The served Reader as a cached LLM (pool: a fixed adapter, identical requests replay)."""
        spec = {'model': model, 'base_url': self.a.reader_base_url, 'api_key_env': '', 'extra_body': NOTHINK}
        return endpoint(spec, self.a.cache, self.meter, max_tokens=self.a.max_tokens)

    def play(self, q, reader, seed):
        ep = run_episode(reader, self.memories[q.history_id], q, self.a.t_max, temperature=1.0, seed=seed)
        v = self.judge.complete(judge_messages(q.category, q.question, q.reference, ep['prediction']), stage='judge')
        correct = parse_label(v.text)
        n_ans = count_tokens(ep['prediction'])
        r, r_task, r_fmt, r_len = reward(correct, ep['format_ok'], n_ans, self.a.lambda_f, self.a.len_start,
                                         self.a.len_end, self.a.len_penalty)
        return {'question_id': q.question_id, 'category': q.category, 'correct': correct, 'judge_raw': v.text,
                'reward': r, 'r_task': r_task, 'r_fmt': r_fmt, 'r_len': r_len, 'answer_tokens': n_ans} | ep


def pool_stats(rows, G):
    """Per question: n correct of G; the pool = questions with 0 < n_correct < G."""
    by_q = defaultdict(list)
    for r in rows:
        by_q[r['question_id']].append(r)
    cat = {qid: rs[0]['category'] for qid, rs in by_q.items()}
    n_ok = {qid: sum(bool(r['correct']) for r in rs) for qid, rs in by_q.items() if len(rs) == G}
    mixed = sorted(qid for qid, n in n_ok.items() if 0 < n < G)
    per_cat = defaultdict(Counter)
    for qid, n in n_ok.items():
        per_cat[cat[qid]]['mixed' if 0 < n < G else 'all_correct' if n == G else 'all_wrong'] += 1
    return {'G': G, 'questions_complete': len(n_ok), 'mixed': len(mixed),
            'mixed_share': len(mixed) / max(len(n_ok), 1),
            'mean_acc': sum(n_ok.values()) / max(G * len(n_ok), 1),
            'searched_share': sum(bool(r['searches']) for r in rows) / max(len(rows), 1),
            'format_ok_share': sum(bool(r['format_ok']) for r in rows) / max(len(rows), 1),
            'n_correct_dist': dict(sorted(Counter(n_ok.values()).items())),
            'by_category': {c: dict(v) for c, v in sorted(per_cat.items())}}, mixed


def pool(a):
    out = Path(a.out) / a.run_name
    out.mkdir(parents=True, exist_ok=True)
    (out / 'config.json').write_text(json.dumps(vars(a), indent=2))
    meter = UsageMeter()
    env = Env(a, meter)
    reader = env.reader(a.reader_model)
    k, n = map(int, a.shard.split('/'))
    path = out / ('pool_rollouts.jsonl' if n == 1 else f'pool_rollouts.s{k}of{n}.jsonl')
    done = {(r['question_id'], r['i']) for f in out.glob('pool_rollouts*.jsonl') for r in read_jsonl(f)}
    qs = sorted(env.questions.values(), key=lambda q: q.question_id)
    if a.limit:
        qs = qs[:a.limit]
    jobs = [(q, i) for j, q in enumerate(qs) if j % n == k for i in range(a.G) if (q.question_id, i) not in done]
    print(f'{len(qs)} questions x {a.G}: {len(jobs)} episodes to run', flush=True)
    lock = threading.Lock()

    def one(job):
        q, i = job
        row = {'i': i, 'adapter': a.reader_model} | env.play(q, reader, seed=i)
        with lock, open(path, 'a') as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + '\n')

    with ThreadPoolExecutor(a.workers) as ex:
        for k, _ in enumerate(ex.map(one, jobs)):
            if k % 500 == 0:
                print(k, json.dumps(meter.snapshot()), flush=True)
    if n > 1:  # pool.json comes from a --shard 0/1 run afterwards (nothing left to play; merges every shard)
        print('SHARD DONE', flush=True)
        return
    rows = [r for f in sorted(out.glob('pool_rollouts*.jsonl')) for r in read_jsonl(f)]
    stats, mixed = pool_stats([r for r in rows if r['question_id'] in {q.question_id for q in qs}], a.G)
    (out / 'pool.json').write_text(json.dumps({'question_ids': mixed, **stats}, indent=2))
    print('DONE', json.dumps(stats), flush=True)


def serve(a):
    out = Path(a.out) / a.run_name
    out.mkdir(parents=True, exist_ok=True)
    (out / 'serve_config.json').write_text(json.dumps(vars(a), indent=2))
    meter = UsageMeter()
    env = Env(a, meter)
    ex = ThreadPoolExecutor(a.workers)
    log_lock = threading.Lock()

    from openai import OpenAI
    client = OpenAI(base_url=a.reader_base_url, api_key='EMPTY')

    def episode(req, q, i, step, call):
        reader = TokenReader(client, req['adapter'], a.max_tokens, meter)
        row = {'i': i, 'step': step, 'call': call, 'adapter': req['adapter']} | \
            env.play(q, reader, seed=step * 100_000 + call * 1000 + i)
        if len(reader.turns) != sum(m['role'] == 'assistant' for m in row['messages']):
            raise RuntimeError(f'{q.question_id}: token turns do not match the episode replies')
        return row | {'turns': reader.turns}

    def rollout(req):
        G, step, call = int(req['G']), int(req.get('seed', 0)), int(req.get('call', 0))
        jobs = [(env.questions[qid], i) for qid in req['question_ids'] for i in range(G)]
        rows = list(ex.map(lambda j: episode(req, j[0], j[1], step, call), jobs))
        with log_lock, open(out / 'rollouts.jsonl', 'a') as fh:
            fh.write(''.join(json.dumps({k: v for k, v in r.items() if k != 'turns'}, ensure_ascii=False) + '\n'
                             for r in rows))
        return rows

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                req = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                body, code = json.dumps({'rows': rollout(req), 'usage': meter.snapshot()}), 200
            except Exception as e:  # the trainer sees the error (and retries); nothing is silently dropped
                traceback.print_exc()
                sys.stdout.flush()
                body, code = json.dumps({'error': f'{type(e).__name__}: {e}'}), 500
            data = body.encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, fmt, *args):
            print(fmt % args, flush=True)

    print(f'serving rollouts on 127.0.0.1:{a.port}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', a.port), Handler).serve_forever()


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    for s in (sub.add_parser('pool'), sub.add_parser('serve')):
        s.add_argument('--run-name', required=True)
        s.add_argument('--reader-base-url', required=True, help='vLLM serving the Reader (tunnel to autoDL-B)')
        s.add_argument('--bank-dir', default='runs/topr1_train_K20/banks')
        s.add_argument('--pool', default='train')
        s.add_argument('--K', type=int, default=20)
        s.add_argument('--t-max', type=int, default=2)
        s.add_argument('--max-tokens', type=int, default=1024)
        s.add_argument('--lambda-f', type=float, default=0.1)
        s.add_argument('--len-start', type=int, default=512, help='answer tokens where the length penalty starts')
        s.add_argument('--len-end', type=int, default=1024, help='answer tokens where it reaches --len-penalty')
        s.add_argument('--len-penalty', type=float, default=0.5)
        s.add_argument('--workers', type=int, default=32, help='episodes in parallel')
        s.add_argument('--gpt-workers', type=int, default=8, help='concurrent luna judge requests')
        s.add_argument('--out', default=str(ROOT / 'runs'))
        s.add_argument('--cache', default=str(ROOT / '.cache' / 'llm.sqlite'))
        if s.prog.endswith('pool'):
            s.add_argument('--reader-model', required=True, help='served name of the SFT Reader adapter')
            s.add_argument('--G', type=int, default=8)
            s.add_argument('--limit', type=int, default=0, help='first n questions only (smoke tests)')
            s.add_argument('--shard', default='0/1', help='k/n: only questions with index %% n == k, rows to '
                           'pool_rollouts.s<k>of<n>.jsonl (one Reader server each); 0/1 merges every shard into pool.json')
        else:
            s.add_argument('--port', type=int, default=18120)
    a = p.parse_args()
    {'pool': pool, 'serve': serve}[a.cmd](a)


if __name__ == '__main__':
    main()
