"""Compare two LLM gold/scenario audits (audit_gold_llm.py outputs) on their common scenarios.

Usage: python evaluation/compare_audits.py OLD_DIR NEW_DIR TAG   (TAG e.g. zh_test+test_ext)
"""
import json
import sys
from collections import Counter
from pathlib import Path


def read(path):
    return [json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]


def rate(rows, pred):
    return sum(map(pred, rows)) / max(1, len(rows))


def main(old_dir, new_dir, tag):
    old, new = read(f'{old_dir}/gold_audit_{tag}.jsonl'), read(f'{new_dir}/gold_audit_{tag}.jsonl')
    common = {r['scenario_id'] for r in old} & {r['scenario_id'] for r in new}
    old, new = [r for r in old if r['scenario_id'] in common], [r for r in new if r['scenario_id'] in common]
    major = lambda r: r.get('severity') == 'major'
    print(f'questions on {len(common)} common scenarios: old {len(old)}, new {len(new)}')
    for name, rows in [('old', old), ('new', new)]:
        print(f'  {name}: not ok {rate(rows, lambda r: r.get("verdict") != "ok"):.1%}, major {rate(rows, major):.1%}')
    for cat in sorted({r['category'] for r in old + new}):
        o, n = [r for r in old if r['category'] == cat], [r for r in new if r['category'] == cat]
        print(f'  {cat:26s} major old {sum(map(major, o)):3d}/{len(o):3d}   new {sum(map(major, n)):3d}/{len(n):3d}')
    so, sn = read(f'{old_dir}/scenario_audit_{tag}.jsonl'), read(f'{new_dir}/scenario_audit_{tag}.jsonl')
    for name, rows in [('old', so), ('new', sn)]:
        c = Counter(f"{i.get('type')}/{i.get('severity')}" for r in rows for i in (r.get('issues') or []))
        print(f'  scenario issues {name}: {sum(c.values()) / max(1, len(rows)):.2f} per scenario, '
              f'major {sum(v for k, v in c.items() if k.endswith("major")) / max(1, len(rows)):.2f} -- {dict(c)}')


if __name__ == '__main__':
    main(*sys.argv[1:4])
