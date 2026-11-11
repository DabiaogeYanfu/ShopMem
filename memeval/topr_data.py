"""TOPR training data for ShopMem-Writer from one sampling round (v2 phase 2; 方法论_v2 §3.3.3).

Per window of a `memeval.teacher` run (backbone + n Writer samples + teacher samples, all from the
same prefix bank), every sample gets the scalar reward of §3.3.1 by the window's role:
  qa        r = r_task + λ1·r_ind + λ2·r_evid
  evidence  r = λ1·r_ind + λ2·r_evid
  noop/other r = λ1·r_ind              (no reward for writing less: no fit to the question set)
(the compression term λ3·Comp is not used yet). The advantage is â = r − mean_window(r) − shift
(--baseline-shift lowers the share of positives; TOPR works best near 10–20 %). Windows whose rewards
spread by less than --min-spread carry no signal and are skipped whole (with a shift > 0 they would
otherwise turn every sample into a negative: e.g. evidence windows, where r_evid is always 1). Samples with
|â| < --min-adv carry no signal and are dropped; so are teacher samples with â < 0 (their behaviour
probability is unknown, so they cannot be importance-weighted; positives need none). Every kept
sample becomes the extractor and manager chat samples of memeval.sft_data (single, complete,
cleanly parsing replies only; the baseline mean still counts every sample) with its advantage.
Windows are split train/val by the same hash as the SFT data. Outputs, under runs/<run>/topr/:
  train.jsonl / val.jsonl   sft_data chat samples + advantage, reward, policy
  stats.json                reward / advantage spread, positive share, counts by role and policy

    python -m memeval.topr_data --run-name topr1_train_K20
"""
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from .data import ROOT, load, read_jsonl
from .evidence_map import window_map
from .sft_data import chat_samples, clean_reply, is_val


def reward(role, row, rw, l_ind, l_evid):
    """Scalar reward of one sample, or None while its qa / evidence rewards are not scored yet."""
    r_ind = row['rind']['score'] or 0.0
    if role == 'qa':
        if rw is None or rw.get('r_task') is None:
            return None
        return rw['r_task'] + l_ind * r_ind + l_evid * (rw.get('r_evid') or 0.0)
    if role == 'evidence':
        return None if rw is None else l_ind * r_ind + l_evid * (rw.get('r_evid') or 0.0)
    return l_ind * r_ind


def build(a):
    run = Path(a.out) / a.run_name
    cfg = json.loads((run / 'config.json').read_text())
    histories, questions, gold = load('en', cfg['pool'], cfg['K'])
    rewards = defaultdict(dict)
    for r in read_jsonl(run / 'rewards.jsonl') if (run / 'rewards.jsonl').exists() else []:
        if r['answerer'] == a.answerer:
            rewards[(r['history_id'], r['window'])][r['sample']] = r
    mc = cfg['method_config']
    splits, counts, advs, spread = {'train': [], 'val': []}, Counter(), [], []
    for h in histories:
        f = run / 'samples' / f'{h.history_id}.jsonl'
        if not f.exists():
            continue
        wmap = {r['window']: r for r in window_map(h, questions, gold.low_density[h.history_id],
                                                   int(mc.get('window_size', 16)), int(mc.get('overlap', 2)))[0]}
        by_window = defaultdict(list)
        for r in read_jsonl(f):
            by_window[r['window']].append(r)
        for j, rows in sorted(by_window.items()):
            role, rw = wmap[j]['role'], rewards.get((h.history_id, j), {})
            rs = [reward(role, r, rw.get(r['sample']), a.lambda_ind, a.lambda_evid) for r in rows]
            if any(x is None for x in rs):
                counts[f'{role}:pending'] += 1
                continue
            spread.append(max(rs) - min(rs))
            if spread[-1] < a.min_spread:
                counts[f'{role}:flat_window'] += 1
                continue
            base = float(np.mean(rs)) + a.baseline_shift
            split = 'val' if is_val(h.history_id, j, a.val_share) else 'train'
            for row, r in zip(rows, rs):
                adv, policy = r - base, row.get('policy', 'writer')
                if abs(adv) < a.min_adv:
                    counts['no_signal'] += 1
                    continue
                if policy == 'teacher' and adv < 0:
                    counts['teacher_negative_dropped'] += 1
                    continue
                if clean_reply(row['extract']) is None or clean_reply(row['manage']) is None:
                    counts['unclean_reply'] += 1
                    continue
                counts[f'{policy}:{"pos" if adv > 0 else "neg"}'] += 1
                advs.append(adv)
                meta = {'history_id': h.history_id, 'window': j, 'sample': row['sample'], 'window_role': role,
                        'policy': policy, 'reward': r, 'advantage': adv}
                splits[split] += chat_samples(row, meta)
    out = run / 'topr'
    out.mkdir(exist_ok=True)
    for name, rows in splits.items():
        (out / f'{name}.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
    pos = sum(v for k, v in counts.items() if k.endswith(':pos'))
    stats = {'run': a.run_name, 'answerer': a.answerer, 'lambda_ind': a.lambda_ind, 'lambda_evid': a.lambda_evid,
             'baseline_shift': a.baseline_shift, 'min_adv': a.min_adv, 'min_spread': a.min_spread, 'counts': dict(sorted(counts.items())),
             'positive_share': pos / len(advs) if advs else None,
             'advantage_pct': {p: float(np.percentile(advs, p)) for p in (1, 10, 50, 90, 99)} if advs else {},
             'windows_with_spread': sum(s > 0 for s in spread), 'windows_scored': len(spread),
             'samples': {n: dict(Counter(r['writer_role'] for r in rows)) for n, rows in splits.items()}}
    (out / 'stats.json').write_text(json.dumps(stats, indent=2))
    print(json.dumps(stats, indent=1))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--run-name', required=True, help='a memeval.teacher sampling round (samples/ + rewards.jsonl)')
    p.add_argument('--answerer', default='luna', choices=['luna', 'qwen8b'])
    p.add_argument('--lambda-ind', type=float, default=0.5)
    p.add_argument('--lambda-evid', type=float, default=0.5)
    p.add_argument('--baseline-shift', type=float, default=0.0)
    p.add_argument('--min-adv', type=float, default=1e-6)
    p.add_argument('--min-spread', type=float, default=0.0, help='skip windows whose reward spread is below this')
    p.add_argument('--val-share', type=float, default=0.05)
    p.add_argument('--out', default=str(ROOT / 'runs'))
    build(p.parse_args())


if __name__ == '__main__':
    main()
