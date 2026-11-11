"""Export a stratified sample of LLM-audit verdicts for human review (auditor calibration).

For each question category, samples up to `--flagged` questions the auditor marked major and
`--ok` questions it marked ok, and writes a Markdown sheet with the evidence turns, the
question, the gold, the auditor's verdict/explanation, and an empty "human verdict" line.
A JSONL copy (same order, with a `human` field to fill) is written next to it so the filled
verdicts can be scored against the auditor.

Usage: python evaluation/export_audit_sample.py --lang en --tag en_test+test_ext --flagged 3 --ok 1
"""
import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from memeval.data import DATA_DIRS, SPLITS, read_jsonl  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--lang', default='en')
    p.add_argument('--tag', default='en_test+test_ext')
    p.add_argument('--audit-dir', default=str(ROOT / 'results' / 'data_audit'))
    p.add_argument('--flagged', type=int, default=3)
    p.add_argument('--ok', type=int, default=1)
    p.add_argument('--seed', type=int, default=0)
    a = p.parse_args()

    src = ROOT / 'data' / DATA_DIRS[a.lang]
    turns = {t['dialog_id']: t for sp in SPLITS for s in read_jsonl(src / sp / 'sessions.jsonl') for t in s['turns']}
    qs = {q['question_id']: q for sp in SPLITS for q in read_jsonl(src / sp / 'questions.jsonl')}
    rows = read_jsonl(Path(a.audit_dir) / f'gold_audit_{a.tag}.jsonl')
    rng = random.Random(a.seed)
    picked = []
    for cat in sorted({r['category'] for r in rows}):
        flagged = [r for r in rows if r['category'] == cat and r.get('severity') == 'major']
        ok = [r for r in rows if r['category'] == cat and r.get('verdict') == 'ok']
        picked += [('flagged', r) for r in rng.sample(flagged, min(a.flagged, len(flagged)))]
        picked += [('ok', r) for r in rng.sample(ok, min(a.ok, len(ok)))]

    md, js = [f'# Audit calibration sample ({a.tag}, {len(picked)} items)\n',
              'Fill "human" with: agree (the auditor is right) / disagree (the auditor is wrong) / unsure.\n'], []
    for n, (kind, r) in enumerate(picked, 1):
        q = qs[r['question_id']]
        ev = '\n'.join(f"    {e} [{turns[e]['turn_type']}] {turns[e]['text']}" for e in q['evidence']) or '    (none)'
        md += [f"## {n}. {r['category']} -- auditor: {r.get('verdict')} / {r.get('severity')} ({kind})",
               f"- scenario: {r['scenario_id']}  question_id: {r['question_id']}  as_of: {q['as_of']}",
               f"- question: {q['question']}", f"- gold: {r['reference']}", f"- evidence turns:\n{ev}",
               f"- auditor explanation: {r.get('explanation', '')}", '- human: ', '']
        js.append({'n': n, 'kind': kind, 'question_id': r['question_id'], 'category': r['category'],
                   'auditor_verdict': r.get('verdict'), 'auditor_severity': r.get('severity'), 'human': None})
    out = Path(a.audit_dir) / f'review_sample_{a.tag}'
    out.with_suffix('.md').write_text('\n'.join(md))
    out.with_suffix('.jsonl').write_text(''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in js))
    print(out.with_suffix('.md'), len(picked))


if __name__ == '__main__':
    main()
