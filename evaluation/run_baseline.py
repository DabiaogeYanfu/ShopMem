"""Simple lexical retrieval baseline.

Reports evidence Recall@K under two retrieval pools:
- raw: every dialog turn is a retrieval candidate;
- gated: only turns with is_low_density == False are candidates, simulating
  a perfect semantic gate. Comparing raw vs gated shows how much the gating
  mechanism the dataset is designed to test should help (or, if evidence
  turns are ever marked low-density, that the gate ground truth is broken).

Also reports corpus-level gating statistics (share of turns marked
low-density, split by turn_type) and, for questions whose gold answer is
free-text (answer.type == "text"), a simple lexical F1 against the
concatenation of gold evidence turns as a sanity baseline for answer
quality (a stand-in for an LLM-judge / ROUGE score, not a substitute).
"""
import argparse
import json
import re
from pathlib import Path


def tokens(s):
    return set(re.findall(r"\w+", s.lower()))


def f1(gold_tokens, pred_tokens):
    if not gold_tokens and not pred_tokens:
        return 1.0
    if not gold_tokens or not pred_tokens:
        return 0.0
    hit = len(gold_tokens & pred_tokens)
    if hit == 0:
        return 0.0
    precision, recall = hit / len(pred_tokens), hit / len(gold_tokens)
    return 2 * precision * recall / (precision + recall)


def load(root):
    turns = []
    for row in (json.loads(x) for x in (root / 'sessions.jsonl').read_text().splitlines() if x.strip()):
        for t in row['turns']:
            turns.append({'dialog_id': t['dialog_id'], 'text': t['text'],
                          'is_low_density': t.get('is_low_density', False),
                          'turn_type': t.get('turn_type', 'human')})
    qs = [json.loads(x) for x in (root / 'questions.jsonl').read_text().splitlines() if x.strip()]
    return turns, qs


def recall_at_k(turns, qs, k):
    recalls = []
    for q in qs:
        qt = tokens(q['question'])
        ranked = sorted(turns, key=lambda z: len(qt & tokens(z['text'])), reverse=True)[:k]
        got = {z['dialog_id'] for z in ranked}
        ev = set(q['evidence'])
        recalls.append(len(got & ev) / len(ev) if ev else 1)
    return sum(recalls) / len(recalls) if recalls else 0


def gating_stats(turns, qs):
    by_id = {t['dialog_id']: t for t in turns}
    total = len(turns)
    low = sum(1 for t in turns if t['is_low_density'])
    by_type = {}
    for t in turns:
        d = by_type.setdefault(t['turn_type'], {'total': 0, 'low_density': 0})
        d['total'] += 1
        d['low_density'] += int(t['is_low_density'])
    evidence_ids = {d for q in qs for d in q['evidence']}
    mislabeled_evidence = [d for d in evidence_ids if by_id.get(d, {}).get('is_low_density')]
    return {
        'total_turns': total,
        'low_density_ratio': round(low / total, 4) if total else 0,
        'by_turn_type': {k: {**v, 'low_density_ratio': round(v['low_density'] / v['total'], 4)}
                          for k, v in by_type.items()},
        'evidence_turns_mislabeled_low_density': len(mislabeled_evidence),
    }


def answer_lexical_f1(turns, qs):
    by_id = {t['dialog_id']: t['text'] for t in turns}
    scores = []
    for q in qs:
        ans = q.get('answer', {})
        if ans.get('type') != 'text' or not ans.get('reference'):
            continue
        ref_tokens = tokens(ans['reference'])
        ev_text = ' '.join(by_id.get(d, '') for d in q['evidence'])
        scores.append(f1(ref_tokens, tokens(ev_text)))
    return {'text_questions': len(scores), 'gold_vs_evidence_lexical_f1': round(sum(scores) / len(scores), 4) if scores else None}


def main(data_dir, k):
    root = Path(data_dir)
    turns, qs = load(root)
    gated_turns = [t for t in turns if not t['is_low_density']]

    result = {
        'questions': len(qs),
        'top_k': k,
        'evidence_recall_at_k_raw': round(recall_at_k(turns, qs, k), 4),
        'evidence_recall_at_k_gated': round(recall_at_k(gated_turns, qs, k), 4) if gated_turns else None,
        'gating_stats': gating_stats(turns, qs),
        'answer_sanity': answer_lexical_f1(turns, qs),
    }
    wm_path = root / 'working_memory.jsonl'
    result['working_memory_snapshots'] = sum(1 for x in wm_path.read_text().splitlines() if x.strip()) if wm_path.exists() else 0
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--data-dir', default='data/dev')
    p.add_argument('--top-k', type=int, default=5)
    a = p.parse_args()
    main(a.data_dir, a.top_k)
