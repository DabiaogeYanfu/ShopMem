"""Offline reward gate, stage B: event-level retrieval-hit reward on the topr1 rollouts.
For every qa window and every sample, rebuild the bank before the window + the sample's write (as
memeval.teacher score does), re-run retrieval (closed_view=rule, planner/embeddings/rerank from cache)
and record per question: event-level in_memory / hit (every required event has a source turn in
memory / in the retrieved provenance), plus the stored luna verdict. Output rows -> argv[2] (jsonl)."""
import json, sys, threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from memeval.data import load, read_jsonl
from memeval.evidence_map import window_map
from memeval.memory_ops import MemoryBank, Unit
from memeval.methods.shopmem_writer import ShopMemWriter
from memeval.revid import covered_turns
from memeval.teacher import QWEN8B, _write_from_row, endpoint
from memeval.llm import UsageMeter
from memeval.data import ROOT
run, out = Path(sys.argv[1]), Path(sys.argv[2])
cfg = json.loads((run / 'config.json').read_text())['method_config']
hs, qs, gold = load('en', 'train', 20)
by_q = {q.question_id: q for q in qs}
R = defaultdict(dict)
for r in read_jsonl(run / 'rewards.jsonl'):
    if r['answerer'] == 'luna' and r['role'] == 'qa':
        R[(r['history_id'], r['window'])][r['sample']] = r
meter = UsageMeter()
qwen = endpoint(QWEN8B, str(ROOT / '.cache' / 'llm.sqlite'), meter)
done = {(x['history_id'], x['window'], x['sample']) for x in read_jsonl(out)} if out.exists() else set()
lock = threading.Lock()
jobs = []
for h in hs:
    f = run / 'samples' / f'{h.history_id}.jsonl'
    if not f.exists():
        continue
    backbone = [Unit.from_dict(d) for d in read_jsonl(run / 'banks' / f'{h.history_id}.jsonl')]
    for row in read_jsonl(f):
        if (h.history_id, row['window']) in R and (h.history_id, row['window'], row['sample']) not in done:
            jobs.append((h, backbone, row))
print(len(jobs), 'sample writes to replay', flush=True)
def one(job):
    h, backbone, row = job
    j = row['window']
    w_ = ShopMemWriter(qwen, 'en', **cfg)
    windows, _ = w_.setup(h)
    bank = MemoryBank.before(backbone, j)
    w_.apply_write(bank, windows[j], _write_from_row(row))
    w_.bank, w_._views = bank, {}
    cov = covered_turns(bank.units)
    stored = {q['question_id']: q for q in R[(h.history_id, j)][row['sample']]['questions']}
    res = []
    for qid, sq in stored.items():
        q = by_q[qid]
        ev = [gold.event_turns.get(e, ()) for e in q.required_events]
        prov = set(w_.retrieve(q.query()).provenance)
        res.append({'question_id': qid, 'category': q.category, 'correct': sq['correct'],
                    'ev_in_mem': sum(any(t in cov for t in ts) for ts in ev) / max(1, len(ev)),
                    'ev_hit': sum(any(t in prov for t in ts) for ts in ev) / max(1, len(ev)),
                    'turn_retrieved_stored': sq['evidence_retrieved']})
    with lock, open(out, 'a') as fh:
        fh.write(json.dumps({'history_id': h.history_id, 'window': j, 'sample': row['sample'],
                             'policy': row.get('policy', 'writer'), 'questions': res}) + '\n')
with ThreadPoolExecutor(int(sys.argv[3]) if len(sys.argv) > 3 else 16) as pool:
    for i, _ in enumerate(pool.map(one, jobs)):
        if i % 200 == 0:
            print(i, json.dumps(meter.snapshot()), flush=True)
print('DONE', json.dumps(meter.snapshot()), flush=True)
