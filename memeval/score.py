"""Judge a run's predictions and aggregate per-category and efficiency metrics.

    python -m memeval.score runs/<run-name> --judge-model qwen-max --base-url ... --api-key-env ...

Writes scores.jsonl (per question: judge label, F1, BLEU-1, evidence recall), summary.json
and summary.md. Judgments are cached in scores.jsonl, so re-scoring only judges new rows.
Headline accuracy is the macro average over categories (each category has the same
number of questions in the full benchmark); micro accuracy is reported alongside.
"""
import argparse
import json
import re
import statistics as st
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .data import load
from .llm import make_llm
from .metrics import bleu1, event_recall, f1, turn_recall
from .prompts import judge_batch_messages, judge_messages

_LABEL = re.compile(r'\b(CORRECT|WRONG)\b')


def parse_label(text):
    try:
        label = json.loads(text[text.index('{'): text.rindex('}') + 1]).get('label', '')
    except (ValueError, json.JSONDecodeError):
        found = _LABEL.findall(text)
        label = found[-1] if found else ''
    return label.upper() == 'CORRECT'


def mean(xs):
    xs = [x for x in xs if x is not None]
    return st.mean(xs) if xs else None


def parse_batch(text, ids):
    """{item id: (correct, raw verdict)} for the items a batched reply judged."""
    try:
        results = json.loads(text[text.index('{'): text.rindex('}') + 1]).get('results', [])
    except (ValueError, json.JSONDecodeError, AttributeError):
        return {}
    out = {}
    for r in results if isinstance(results, list) else []:
        if isinstance(r, dict) and r.get('id') in ids and str(r.get('label', '')).upper() in ('CORRECT', 'WRONG'):
            out[r['id']] = (str(r['label']).upper() == 'CORRECT', json.dumps(r, ensure_ascii=False))
    return out


def score_run(run_dir, judge=None, workers=8, batch=1):
    """judge=None computes everything except judge accuracy (left empty). batch > 1 judges up to
    `batch` items of one category per request; an item the reply leaves out is judged alone."""
    run_dir = Path(run_dir)
    config = json.loads((run_dir / 'config.json').read_text())
    lang = config['lang']
    _, questions, gold = load(lang, config['pool'], config['K'])
    evidence = {q.question_id: q.evidence for q in questions}
    required_events = {q.question_id: q.required_events for q in questions}
    preds = [json.loads(x) for x in (run_dir / 'predictions.jsonl').read_text().splitlines() if x.strip()]
    score_path = run_dir / 'scores.jsonl'
    scored = {}
    if score_path.exists():
        scored = {r['question_id']: r for r in map(json.loads, score_path.read_text().splitlines())}

    def judge_one(p):
        c = judge.complete(judge_messages(p['category'], p['question'], p['reference'], p['prediction']), stage='judge')
        return parse_label(c.text), c.text

    def judge_chunk(chunk):
        items = [(i, p['question'], p['reference'], p['prediction']) for i, p in enumerate(chunk, 1)]
        c = judge.complete(judge_batch_messages(chunk[0]['category'], items), stage='judge',
                           max_tokens=256 * len(chunk))
        got = parse_batch(c.text, set(range(1, len(chunk) + 1)))
        return {p['question_id']: got.get(i) or judge_one(p) for i, p in enumerate(chunk, 1)}

    def score_one(p, verdict=None):
        correct = raw = None
        if judge is not None:
            correct, raw = verdict or judge_one(p)
        return {'question_id': p['question_id'], 'category': p['category'], 'answerable': p['answerable'],
                'correct': correct, 'judge_raw': raw,
                'f1': f1(p['prediction'], p['reference'], lang), 'bleu1': bleu1(p['prediction'], p['reference'], lang),
                'turn_recall': turn_recall(p['provenance'], evidence[p['question_id']]),
                'event_recall': event_recall(p['provenance'], required_events[p['question_id']], gold.event_turns),
                'context_tokens': p['context_tokens'], 'retrieve_s': p['retrieve_s']}

    # rows scored without a judge are re-scored once a judge is available
    todo = [p for p in preds if p['question_id'] not in scored
            or (judge is not None and scored[p['question_id']]['correct'] is None)]
    verdicts = {}
    if judge is not None and batch > 1:
        by_cat = defaultdict(list)
        for p in todo:
            by_cat[p['category']].append(p)
        chunks = [v[i:i + batch] for v in by_cat.values() for i in range(0, len(v), batch)]
        with ThreadPoolExecutor(workers) as pool:
            for got in pool.map(judge_chunk, chunks):
                verdicts.update(got)
    with ThreadPoolExecutor(workers) as pool:
        for row in pool.map(lambda p: score_one(p, verdicts.get(p['question_id'])), todo):
            scored[row['question_id']] = row
    rows = [scored[p['question_id']] for p in preds]
    score_path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))

    by_cat = defaultdict(list)
    for r in rows:
        by_cat[r['category']].append(r)
    cats = {c: {'n': len(v), 'judge_acc': mean(r['correct'] for r in v), 'f1': mean(r['f1'] for r in v),
                'bleu1': mean(r['bleu1'] for r in v), 'turn_recall': mean(r['turn_recall'] for r in v),
                'event_recall': mean(r['event_recall'] for r in v),
                'context_tokens': mean(r['context_tokens'] for r in v)}
            for c, v in sorted(by_cat.items())}
    answerable = [r for r in rows if r['answerable']]
    hist = [json.loads(x) for x in (run_dir / 'histories.jsonl').read_text().splitlines()] \
        if (run_dir / 'histories.jsonl').exists() else []
    usage = json.loads((run_dir / 'usage.json').read_text()) if (run_dir / 'usage.json').exists() else {}
    write, query_time = usage.get('write', {}), usage.get('retrieve', {})
    summary = {
        'run': config['run_name'], 'method': config['method'], 'lang': lang, 'pool': config['pool'], 'K': config['K'],
        'model': config['model'], 'questions': len(rows),
        'judge_acc_macro': mean(v['judge_acc'] for v in cats.values()),
        'judge_acc_micro': mean(r['correct'] for r in rows),
        'judge_acc_answerable': mean(r['correct'] for r in answerable),
        'f1_answerable': mean(r['f1'] for r in answerable), 'bleu1_answerable': mean(r['bleu1'] for r in answerable),
        'turn_recall': mean(r['turn_recall'] for r in rows), 'event_recall': mean(r['event_recall'] for r in rows),
        'context_tokens_mean': mean(r['context_tokens'] for r in rows),
        'retrieve_s_mean': mean(r['retrieve_s'] for r in rows),
        'ingest_s_total': sum(h['ingest_s'] for h in hist),
        'write_llm_calls': write.get('calls', 0),
        'write_tokens': write.get('prompt_tokens', 0) + write.get('completion_tokens', 0),
        'memory_entries': sum(h['memory'].get('entries', 0) for h in hist),
        # LLM calls a method makes at query time (planning, reflection) besides the shared answer call
        'retrieve_llm_calls_per_q': query_time.get('calls', 0) / max(len(rows), 1),
        'retrieve_llm_tokens_per_q': (query_time.get('prompt_tokens', 0)
                                      + query_time.get('completion_tokens', 0)) / max(len(rows), 1),
        'by_category': cats,
    }
    (run_dir / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    (run_dir / 'summary.md').write_text(render(summary))
    print(render(summary))
    return summary


def fmt(x, pct=False):
    if x is None:
        return '–'
    return f'{100 * x:.1f}' if pct else (f'{x:.0f}' if x >= 100 else f'{x:.2f}')


def render(s):
    lines = [f"# {s['run']}", '',
             f"Judge acc (macro / micro / answerable): {fmt(s['judge_acc_macro'], True)} / "
             f"{fmt(s['judge_acc_micro'], True)} / {fmt(s['judge_acc_answerable'], True)}  ",
             f"F1 / BLEU-1 (answerable): {fmt(s['f1_answerable'], True)} / {fmt(s['bleu1_answerable'], True)}  ",
             f"Evidence recall (turn / event): {fmt(s['turn_recall'], True)} / {fmt(s['event_recall'], True)}  ",
             f"Context tokens / question: {fmt(s['context_tokens_mean'])}; retrieve {fmt(s['retrieve_s_mean'])} s; "
             f"ingest total {fmt(s['ingest_s_total'])} s; write-time LLM calls {s['write_llm_calls']:.0f}, "
             f"tokens {s['write_tokens']:.0f}; memory entries {s['memory_entries']}  ",
             f"Query-time LLM calls / question: {s['retrieve_llm_calls_per_q']:.1f} "
             f"({fmt(s['retrieve_llm_tokens_per_q'])} tokens)", '',
             '| category | n | judge acc | F1 | BLEU-1 | turn recall | event recall | ctx tokens |',
             '|---|---|---|---|---|---|---|---|']
    for c, v in s['by_category'].items():
        lines.append(f"| {c} | {v['n']} | {fmt(v['judge_acc'], True)} | {fmt(v['f1'], True)} | {fmt(v['bleu1'], True)} "
                     f"| {fmt(v['turn_recall'], True)} | {fmt(v['event_recall'], True)} | {fmt(v['context_tokens'])} |")
    return '\n'.join(lines) + '\n'


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('run_dir')
    p.add_argument('--judge-model', default='qwen-max')
    p.add_argument('--base-url')
    p.add_argument('--api-key-env', default='DASHSCOPE_API_KEY')
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--workers', type=int, default=4, help='concurrent judge requests')
    p.add_argument('--batch', type=int, default=1, help='items of one category per judge request')
    p.add_argument('--cache')
    a = p.parse_args()
    judge = make_llm(a.judge_model, a.base_url, a.api_key_env, dry_run=a.dry_run, cache_path=a.cache, max_tokens=256)
    score_run(a.run_dir, judge, workers=a.workers, batch=a.batch)


if __name__ == '__main__':
    main()
