"""Window -> evidence-question map for Writer credit assignment (v2 实验计划 phase 1, step 2).

Each question is attached to the window where its **last** gold evidence turn (dialogue or
tool observation) first appears: once that window is written, the memory prefix can hold
all of the question's evidence, so r_task / r_evid of that window are computed on it. A
turn in the overlap of two windows belongs to the earlier one. Questions without evidence
(unanswerable) attach to no window and are listed separately.

Per window the map also records the gold evidence turns that first appear in it (from any
question, including ones attached later: the dense signal r_evid reads) and the low-density
labels of its new turns. Roles:
  qa        at least one question attached
  evidence  no question attached, but holds evidence turns of later questions
  noop      neither, and every new turn is low-density: the NOOP supervision windows
  other     neither, with some high-density turn (r_ind only)
The map reads Gold (evidence, low_density), so it is training / analysis data only; the
Writer itself never sees it.

    python -m memeval.evidence_map --lang en --pool dev30 --K 30
writes results/window_map/<lang>_<pool>_K<K>.jsonl (one row per window) and .stats.json.
"""
import argparse
import json
from collections import Counter

from .data import ROOT, load
from .windows import session_windows


def window_map(history, questions, low_density, window_size=16, overlap=2):
    """Rows (one per window of `history`) and the ids of questions left unattached."""
    windows = session_windows(history, window_size, overlap)
    first = {}  # turn id -> index of the first window containing it
    for w in windows:
        for tid in w.new_turns:
            first.setdefault(tid, w.index)
    order = {t.dialog_id: i for i, t in enumerate(history.turns())}
    attached, evid_here, unattached = {}, {}, []
    for q in questions:
        if q.history_id != history.history_id:
            continue
        if not q.evidence:
            unattached.append(q.question_id)
            continue
        last = max(q.evidence, key=order.__getitem__)
        attached.setdefault(first[last], []).append(q)
        for e in q.evidence:
            evid_here.setdefault(first[e], set()).add(e)
    rows = []
    for w in windows:
        qs = attached.get(w.index, [])
        ev = sorted(evid_here.get(w.index, ()))
        low = [low_density[t] for t in w.new_turns]
        role = 'qa' if qs else 'evidence' if ev else 'noop' if all(low) else 'other'
        rows.append({'history_id': history.history_id, 'window': w.index, 'session_id': w.session_id,
                     'turns': w.turn_ids, 'new_turns': list(w.new_turns), 'role': role,
                     'questions': [q.question_id for q in qs], 'categories': [q.category for q in qs],
                     'evidence_turns': ev, 'n_low_density': sum(low), 'n_new': len(low)})
    return rows, unattached


def build(lang, pool, K, window_size=16, overlap=2):
    histories, questions, gold = load(lang, pool, K)
    rows, unattached = [], []
    for h in histories:
        r, u = window_map(h, questions, gold.low_density[h.history_id], window_size, overlap)
        rows += r
        unattached += u
    return rows, unattached, questions


def stats(rows, unattached, questions):
    roles = Counter(r['role'] for r in rows)
    per_q = Counter(len(r['questions']) for r in rows if r['questions'])
    attached = sum(len(r['questions']) for r in rows)
    return {'windows': len(rows), 'roles': dict(roles), 'questions': len(questions), 'attached': attached,
            'unattached': len(unattached),
            'unattached_categories': dict(Counter(q.category for q in questions if q.question_id in set(unattached))),
            'questions_per_qa_window': dict(sorted(per_q.items())),
            'turns_low_density_share': sum(r['n_low_density'] for r in rows) / max(1, sum(r['n_new'] for r in rows))}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--lang', default='en')
    p.add_argument('--pool', default='dev30')
    p.add_argument('--K', type=int, default=30)
    p.add_argument('--window-size', type=int, default=16)
    p.add_argument('--overlap', type=int, default=2)
    p.add_argument('--out', default=str(ROOT / 'results' / 'window_map'))
    a = p.parse_args()
    rows, unattached, questions = build(a.lang, a.pool, a.K, a.window_size, a.overlap)
    from pathlib import Path
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stem = f'{a.lang}_{a.pool}_K{a.K:03d}'
    (out / f'{stem}.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
    s = stats(rows, unattached, questions) | {'window_size': a.window_size, 'overlap': a.overlap}
    (out / f'{stem}.stats.json').write_text(json.dumps(s, ensure_ascii=False, indent=2))
    print(json.dumps(s, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
