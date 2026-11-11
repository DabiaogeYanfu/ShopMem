"""Reader signal gate: is there GRPO signal on the READ side? (2026-10-09, no training.)
On the SFT Writer's final train banks (runs/topr1_train_K20/banks, K=20, 4 histories) a stratified
question sample is answered by a prototype reader agent: Qwen3-8B at T=1 gets the v1 retrieval
(closed_view=rule), may search again up to MAX_SEARCH times (own query, include closed versions or
not), then answers. n samples per question (request seed k) + one greedy answer of the current
pipeline (k = -1, the reference). luna judges every answer. Rows -> argv[1] (jsonl, resumable).
Gate: share of questions whose n samples are mixed (some right, some wrong) >= .30.

    python scripts/reader_gate.py runs/reader_gate/rows.jsonl [per_category] [n]
"""
import json, random, sys, threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from memeval.data import ROOT, Query, load, read_jsonl
from memeval.llm import UsageMeter
from memeval.memory_ops import MemoryBank, Unit
from memeval.methods.shopmem_writer import ShopMemWriter
from memeval.prompts import ANSWER_SYSTEM, answer_messages, judge_messages, parse_answer
from memeval.score import parse_label
from memeval.teacher import LUNA, QWEN8B, endpoint

RUN = ROOT / 'runs' / 'topr1_train_K20'
MAX_SEARCH = 2
TOOLS = (
    '\nBefore answering you may search the memory again. Reply with JSON only, one of:\n'
    '{"reasoning": "...", "action": "search", "query": "<what to look for>", "include_closed": true or false}\n'
    '{"reasoning": "...", "action": "answer", "answer": "<final reply>"}\n'
    'include_closed=true also returns entries that are no longer valid (earlier states, superseded values, '
    'constraints of closed work orders); false returns only what is valid now. '
    f'You may search at most {MAX_SEARCH} times, then you must answer.')
USER = 'Memory (first retrieval for the question):\n{context}\n\nQuestion: {question}'
RESULT = 'Search results for "{query}" (include_closed={closed}):\n{context}'
FORCE = 'No more searches. Give your final answer now as {"reasoning": "...", "action": "answer", "answer": "..."}.'


def parse(text):
    try:
        obj = json.loads(text[text.index('{'): text.rindex('}') + 1])
        return obj if isinstance(obj, dict) else {}
    except (ValueError, json.JSONDecodeError):
        return {}


def main():
    out = Path(sys.argv[1])
    per_cat = int(sys.argv[2]) if len(sys.argv) > 2 else 23
    n = int(sys.argv[3]) if len(sys.argv) > 3 else 8
    out.parent.mkdir(parents=True, exist_ok=True)
    cfg = json.loads((RUN / 'config.json').read_text())['method_config']
    hs, qs, _ = load('en', 'train', 20)
    by_cat = defaultdict(list)
    for q in qs:
        by_cat[q.category].append(q)
    rng = random.Random(0)
    picked = [q for c in sorted(by_cat) for q in rng.sample(by_cat[c], per_cat)]

    meter, sem = UsageMeter(), threading.BoundedSemaphore(8)
    cache = str(ROOT / '.cache' / 'llm.sqlite')
    qwen = endpoint(QWEN8B, cache, meter)
    judge = endpoint(LUNA, cache, meter, sem, max_tokens=256)
    readers = {}
    for h in hs:
        w = ShopMemWriter(qwen, 'en', **cfg)
        w.setup(h)
        bank = MemoryBank()
        for d in read_jsonl(RUN / 'banks' / f'{h.history_id}.jsonl'):
            u = Unit.from_dict(d)
            bank.units.append(u)
            bank.by_id[u.uid] = u
        w.bank, w._views = bank, {}
        readers[h.history_id] = w

    done = {(r['question_id'], r['sample']) for r in read_jsonl(out)} if out.exists() else set()
    jobs = [(q, k) for q in picked for k in [-1] + list(range(n)) if (q.question_id, k) not in done]
    print(len(picked), 'questions,', len(jobs), 'answers to run', flush=True)
    lock = threading.Lock()

    def search(w, q, text, closed):
        return w._view(q.as_of, bool(closed)).retrieve(Query(q.question_id, q.history_id, text, q.as_of))

    def one(job):
        q, k = job
        w = readers[q.history_id]
        first = w.retrieve(q.query())
        trace = []
        if k < 0:  # the current pipeline, greedy
            c = qwen.complete(answer_messages('en', first.context, q.question, q.as_of, w.calendar_text, True),
                              stage='answer', max_tokens=1024)
            pred = parse_answer(c.text)
        else:
            system = ANSWER_SYSTEM['en'].format(as_of=q.as_of, calendar=w.calendar_text) + TOOLS
            msgs = [{'role': 'system', 'content': system},
                    {'role': 'user', 'content': USER.format(context=first.context, question=q.question)}]
            pred = None
            for step in range(MAX_SEARCH + 2):
                c = qwen.complete(msgs, stage='answer', max_tokens=1024, temperature=1.0, seed=k)
                obj = parse(c.text)
                msgs.append({'role': 'assistant', 'content': c.text})
                if obj.get('action') == 'search' and obj.get('query') and step < MAX_SEARCH:
                    closed = bool(obj.get('include_closed'))
                    r = search(w, q, str(obj['query']), closed)
                    trace.append({'query': str(obj['query']), 'closed': closed})
                    msgs.append({'role': 'user', 'content': RESULT.format(query=obj['query'], closed=closed,
                                                                          context=r.context or '(nothing found)')})
                elif obj.get('action') == 'search' or (not obj and step <= MAX_SEARCH):
                    msgs.append({'role': 'user', 'content': FORCE})
                else:
                    pred = str(obj['answer']).strip() if obj.get('answer') else parse_answer(c.text)
                    break
            if pred is None:
                pred = parse_answer(msgs[-1]['content'])
        v = judge.complete(judge_messages(q.category, q.question, q.reference, pred), stage='judge')
        row = {'question_id': q.question_id, 'history_id': q.history_id, 'category': q.category, 'sample': k,
               'prediction': pred, 'correct': parse_label(v.text), 'searches': trace}
        with lock, open(out, 'a') as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + '\n')

    with ThreadPoolExecutor(24) as pool:
        for i, _ in enumerate(pool.map(one, jobs)):
            if i % 200 == 0:
                print(i, json.dumps(meter.snapshot()), flush=True)
    print('DONE', json.dumps(meter.snapshot()), flush=True)


if __name__ == '__main__':
    main()
