"""Run a memory method on M-LoCoMo-Long: ingest each history, answer its questions, then score.

    python -m memeval.run --method full_context --lang zh --pool dev --K 10 \
        --model qwen-plus --base-url https://dashscope.aliyuncs.com/compatible-mode/v1 \
        --api-key-env DASHSCOPE_API_KEY --judge-model qwen-max
    python -m memeval.run --method observation_mask --lang zh --pool dev --K 1 --dry-run --limit-per-category 2

Outputs runs/<run-name>/: config.json, histories.jsonl (ingest time, memory size, write
usage per history), predictions.jsonl (one row per question, appended as they finish,
so an interrupted run resumes where it stopped), usage.json, then the files written by
memeval.score.
"""
import argparse
import json
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import score
from .data import ROOT, load
from .llm import UsageMeter, count_tokens, make_llm
from .methods import METHODS
from .prompts import answer_messages, parse_answer, render_calendar


def parse_kv(pairs):
    out = {}
    for p in pairs or []:
        k, v = p.split('=', 1)
        try:
            out[k] = json.loads(v)
        except json.JSONDecodeError:
            out[k] = v
    return out


def select_questions(questions, limit_per_category):
    if not limit_per_category:
        return questions
    seen, out = defaultdict(int), []
    for q in questions:
        if seen[q.category] < limit_per_category:
            seen[q.category] += 1
            out.append(q)
    return out


def run(args):
    method_cls = METHODS[args.method]
    method_cfg = parse_kv(args.method_config)
    run_name = args.run_name or f'{args.method}_{args.lang}_{args.pool}_K{args.K}_{args.model}'
    out = Path(args.out) / run_name
    out.mkdir(parents=True, exist_ok=True)
    config = {k: v for k, v in vars(args).items() if k != 'func'} | {'method_config': method_cfg, 'run_name': run_name}
    (out / 'config.json').write_text(json.dumps(config, ensure_ascii=False, indent=2))

    histories, questions, gold = load(args.lang, args.pool, args.K)
    questions = select_questions(questions, args.limit_per_category)
    if args.max_histories:
        keep = {h.history_id for h in histories[:args.max_histories]}
        histories = [h for h in histories if h.history_id in keep]
        questions = [q for q in questions if q.history_id in keep]
    calendar_text = render_calendar(gold.calendars, args.lang)

    pred_path = out / 'predictions.jsonl'
    done = {json.loads(x)['question_id'] for x in pred_path.read_text().splitlines()} if pred_path.exists() else set()
    meter = UsageMeter()
    llm = make_llm(args.model, args.base_url, args.api_key_env, dry_run=args.dry_run, cache_path=args.cache,
                   meter=meter, max_tokens=args.max_answer_tokens, extra_body=args.extra_body)
    write_lock = threading.Lock()
    by_history = defaultdict(list)
    for q in questions:
        if q.question_id not in done:
            by_history[q.history_id].append(q)

    for h in histories:
        todo = by_history.get(h.history_id)
        if not todo:
            continue
        method = method_cls(llm, args.lang, gold=gold, **method_cfg)
        before = meter.snapshot().get('write', {})
        t0 = time.perf_counter()
        method.ingest(h)
        ingest_s = time.perf_counter() - t0
        after = meter.snapshot().get('write', {})
        write_usage = {k: after.get(k, 0) - before.get(k, 0) for k in after}
        with open(out / 'histories.jsonl', 'a') as f:
            f.write(json.dumps({'history_id': h.history_id, 'turns': len(h.turns()), 'ingest_s': ingest_s,
                                'memory': method.memory_stats(), 'write_usage': write_usage}) + '\n')
        retrieve_lock = threading.Lock() if not getattr(method, 'thread_safe', False) else None

        def answer_one(q):
            if hasattr(method, 'answer'):  # an agent that retrieves and answers itself (shopmem_reader)
                t1 = time.perf_counter()
                got = method.answer(q, llm)
                row = {'question_id': q.question_id, 'history_id': q.history_id, 'category': q.category,
                       'answerable': q.answerable, 'question': q.question, 'reference': q.reference,
                       'answer_s': time.perf_counter() - t1} | got  # answer_s: whole episode; retrieve_s: its retrievals
                with write_lock, open(pred_path, 'a') as f:
                    f.write(json.dumps(row, ensure_ascii=False) + '\n')
                return
            t1 = time.perf_counter()
            if retrieve_lock:
                with retrieve_lock:
                    r = method.retrieve(q.query())
            else:
                r = method.retrieve(q.query())
            retrieve_s = time.perf_counter() - t1
            reasoning = args.answer_format == 'reasoning'
            c = llm.complete(answer_messages(args.lang, r.context, q.question, q.as_of, calendar_text, reasoning),
                             stage='answer')
            row = {'question_id': q.question_id, 'history_id': q.history_id, 'category': q.category,
                   'answerable': q.answerable, 'question': q.question, 'reference': q.reference,
                   'prediction': parse_answer(c.text) if reasoning else c.text.strip(), 'context_tokens': count_tokens(r.context),
                   'answer_prompt_tokens': c.prompt_tokens, 'provenance': r.provenance,
                   'retrieve_s': retrieve_s, 'answer_s': c.latency, 'meta': r.meta}
            if reasoning:
                row['raw_answer'] = c.text
            with write_lock, open(pred_path, 'a') as f:
                f.write(json.dumps(row, ensure_ascii=False) + '\n')

        with ThreadPoolExecutor(args.workers) as pool:
            list(pool.map(answer_one, todo))
        print(f'[{run_name}] {h.history_id}: ingested in {ingest_s:.1f}s, answered {len(todo)} questions')

    prev = json.loads((out / 'usage.json').read_text()) if (out / 'usage.json').exists() else {}
    usage = meter.snapshot()
    for stage, vals in prev.items():  # accumulate across resumed invocations
        for k, v in vals.items():
            usage.setdefault(stage, {})[k] = usage.get(stage, {}).get(k, 0) + v
    (out / 'usage.json').write_text(json.dumps(usage, indent=2))
    judge = None
    if not args.no_judge:
        judge = make_llm(args.judge_model or args.model, args.judge_base_url or args.base_url,
                         args.judge_api_key_env or args.api_key_env, dry_run=args.dry_run, cache_path=args.cache,
                         max_tokens=256, extra_body=args.judge_extra_body)
    score.score_run(out, judge, workers=args.judge_workers, batch=args.judge_batch)
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--method', required=True, choices=sorted(METHODS))
    p.add_argument('--method-config', nargs='*', metavar='KEY=VALUE', help='e.g. window_tokens=4000 keep_last=10')
    p.add_argument('--lang', choices=['zh', 'en'], default='zh')
    p.add_argument('--pool', choices=['eval', 'dev', 'dev30', 'test90', 'train'], default='dev')
    p.add_argument('--K', type=int, default=10)
    p.add_argument('--model', default='qwen-plus')
    p.add_argument('--base-url')
    p.add_argument('--api-key-env', default='DASHSCOPE_API_KEY', help="'' for a local server without a key")
    p.add_argument('--judge-model')
    p.add_argument('--judge-base-url')
    p.add_argument('--judge-api-key-env')
    p.add_argument('--extra-body', type=json.loads, metavar='JSON',
                   help='sent with every answer request, e.g. \'{"chat_template_kwargs": {"enable_thinking": false}}\'')
    p.add_argument('--judge-extra-body', type=json.loads, metavar='JSON')
    p.add_argument('--dry-run', action='store_true', help='no network: stub answers and judgments')
    p.add_argument('--no-judge', action='store_true')
    p.add_argument('--limit-per-category', type=int, default=0)
    p.add_argument('--max-histories', type=int, default=0)
    p.add_argument('--max-answer-tokens', type=int, default=512)
    p.add_argument('--answer-format', choices=['plain', 'reasoning'], default='plain',
                   help='reasoning: every method answers as JSON {reasoning, answer}; only answer is scored')
    p.add_argument('--workers', type=int, default=8, help='concurrent answer requests (local model)')
    p.add_argument('--judge-workers', type=int, default=4, help='concurrent judge requests (external API)')
    p.add_argument('--judge-batch', type=int, default=1, help='items of one category per judge request')
    p.add_argument('--cache', default=str(ROOT / '.cache' / 'llm.sqlite'))
    p.add_argument('--out', default=str(ROOT / 'runs'))
    p.add_argument('--run-name')
    out = run(p.parse_args())
    print(f'done -> {out}')


if __name__ == '__main__':
    main()
