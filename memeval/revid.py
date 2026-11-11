"""r_evid and M-Fail: rule-computed evidence coverage of the memory (v2 实验计划 phase 1, step 5).

A memory unit covers the turns it was written from (source_turns); every version counts,
closed ones included, since questions about earlier states are answered from them.

r_evid (Writer reward, one window): the share of the gold evidence turns that first appear
in the window (evidence_map `evidence_turns`) covered by the memory after the window's write.
None when the window holds no evidence turn.

M-Fail (evaluation, after Memory-R2): the share of answerable questions whose gold evidence
is not all in memory. `failure_breakdown` sorts every answerable question into
  correct      judged correct
  memory       wrong, and some evidence turn is in no memory unit
  retrieval    wrong, all evidence in memory but not all of it in the retrieved context
  reasoning    wrong, all evidence retrieved
The memory is read from the bank files ShopMem-Writer writes with bank_path.

    python -m memeval.revid --run runs/<run> --banks runs/<run>/banks
"""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

from .data import load, read_jsonl
from .memory_ops import Unit


def covered_turns(units):
    return {t for u in units for t in u.source_turns}


def coverage(evidence, covered):
    evidence = set(evidence)
    return len(evidence & covered) / len(evidence) if evidence else None


def r_evid(window_evidence, units):
    return coverage(window_evidence, covered_turns(units))


def failure_breakdown(questions, units_by_history, preds, scores):
    """Rows per answerable question with the failure kind; `preds` / `scores` are keyed by question id."""
    covered = {h: covered_turns(us) for h, us in units_by_history.items()}
    rows = []
    for q in questions:
        if not q.answerable or q.question_id not in scores or q.history_id not in covered:
            continue
        in_mem = coverage(q.evidence, covered[q.history_id])
        got = coverage(q.evidence, set(preds[q.question_id]['provenance']))
        correct = scores[q.question_id]['correct']
        kind = ('correct' if correct else 'memory' if in_mem < 1 else 'retrieval' if got < 1 else 'reasoning') \
            if correct is not None else None
        rows.append({'question_id': q.question_id, 'category': q.category, 'evidence_in_memory': in_mem,
                     'evidence_retrieved': got, 'correct': correct, 'kind': kind})
    return rows


def summarize(rows):
    n = len(rows)
    by_cat = defaultdict(list)
    for r in rows:
        by_cat[r['category']].append(r)
    mfail = lambda rs: sum(r['evidence_in_memory'] < 1 for r in rs) / len(rs) if rs else None
    return {'questions': n, 'm_fail': mfail(rows),
            'evidence_in_memory_mean': sum(r['evidence_in_memory'] for r in rows) / n if n else None,
            'kinds': dict(Counter(r['kind'] for r in rows)),
            'by_category': {c: {'m_fail': mfail(rs), 'kinds': dict(Counter(r['kind'] for r in rs))}
                            for c, rs in sorted(by_cat.items())}}


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--run', required=True)
    p.add_argument('--banks', help='directory of <history_id>.jsonl bank files (default <run>/banks)')
    a = p.parse_args()
    run = Path(a.run)
    cfg = json.loads((run / 'config.json').read_text())
    _, questions, _ = load(cfg['lang'], cfg['pool'], cfg['K'])
    banks = Path(a.banks or run / 'banks')
    units = {f.stem: [Unit.from_dict(d) for d in read_jsonl(f)] for f in banks.glob('*.jsonl')}
    preds = {r['question_id']: r for r in read_jsonl(run / 'predictions.jsonl')}
    scores = {r['question_id']: r for r in read_jsonl(run / 'scores.jsonl')} if (run / 'scores.jsonl').exists() else {}
    rows = failure_breakdown(questions, units, preds, scores)
    (run / 'memory_failure.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in rows))
    s = summarize(rows)
    (run / 'memory_failure.json').write_text(json.dumps(s, indent=2))
    print(json.dumps(s, indent=2))


if __name__ == '__main__':
    main()
