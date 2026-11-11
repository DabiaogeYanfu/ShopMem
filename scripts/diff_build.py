"""Diff two Reader SFT builds (e.g. v2 single-vote vs v3 5-vote majority + lapse filter): kept questions per
category, questions dropped / added, and questions whose kept trajectory changed.
    python -m scripts.diff_build runs/reader_cold_sol_K20_all_v2 runs/reader_cold_sol_K20_all_v3
    -> <new>/sft/diff_vs_<old>.json"""
import collections, json, sys
from pathlib import Path
from memeval.data import read_jsonl


def kept(d):
    return {r['question_id']: r for sp in ('train', 'val') for r in read_jsonl(Path(d) / 'sft' / f'{sp}.jsonl')}


old, new = sys.argv[1], sys.argv[2]
A, B = kept(old), kept(new)
cat = collections.defaultdict(collections.Counter)
for q, r in A.items():
    cat[r['category']]['old'] += 1
for q, r in B.items():
    cat[r['category']]['new'] += 1
dropped = sorted(set(A) - set(B))
added = sorted(set(B) - set(A))
changed = sorted(q for q in set(A) & set(B) if (A[q]['run'], A[q]['sample']) != (B[q]['run'], B[q]['sample']))
for q in dropped:
    cat[A[q]['category']]['dropped'] += 1
for q in changed:
    cat[A[q]['category']]['changed'] += 1
out = {'old': old, 'new': new, 'kept_old': len(A), 'kept_new': len(B), 'dropped': len(dropped), 'added': len(added),
       'changed_trajectory': len(changed),
       'new_votes_correct_dist': dict(sorted(collections.Counter(r.get('votes_correct') for r in B.values()).items(),
                                             key=lambda x: str(x[0]))),
       'by_category': {c: dict(v) for c, v in sorted(cat.items())},
       'dropped_ids': dropped, 'added_ids': added, 'changed_ids': changed}
p = Path(new) / 'sft' / f'diff_vs_{Path(old).name}.json'
p.write_text(json.dumps(out, indent=2))
print(json.dumps({k: v for k, v in out.items() if not k.endswith('_ids')}, indent=2))
