"""SFT data for ShopMem-Writer from teacher traces (v2 phase 1, step 7: rejection sampling).

Per window of a `memeval.teacher` run, keep one of its sampled writes (backbone + n resamples):
  1. candidates: r_ind all-pass, and both replies are a single, complete, cleanly parsing
     JSON-Lines reply (a continued or partly unparsable reply is not imitated);
  2. ranking, by the window's role in the evidence map:
       qa        r_task (chosen answerer), then r_evid, r_ind, fewer stored tokens, lower sample
       evidence  r_evid, then r_ind, fewer stored tokens, lower sample
       noop      fewer facts, then the backbone (these windows teach writing nothing)
       other     the backbone, then lower sample (no reward beyond r_ind)
     qa / evidence windows wait until every candidate is scored (`pending` in the stats);
     --min-r-task drops qa windows whose best r_task is below it (default 0: keep all).
     Ablations (--ablate, v2 §3.4): no_rind drops the r_ind all-pass filter and the r_ind ranking
     term; no_revid drops the r_evid ranking terms. Each writes to sft_<ablate>/ instead of sft/.
The kept write becomes two chat samples of the same model: the extractor (its prompt + reply)
and, if it extracted any fact, the manager (its prompt, built from the backbone bank the sample
saw, + reply). Both come from the same sample, so the manager imitates actions on the facts its
own extractor produced. Windows are split train/val (--val-share, by a hash of the window, so
both roles of a window stay together). Outputs, under runs/<run>/sft/:
  train.jsonl / val.jsonl   {"messages": [...], "writer_role": extractor|manager, provenance, rewards}
  selection.jsonl           per window: role, candidates, kept sample and why, or why none
  stats.json                counts, action mix (kept vs all samples), reply/prompt token lengths

    python -m memeval.sft_data --run-name teacher_train_K20 --answerer luna
"""
import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from .data import ROOT, load, read_jsonl
from .evidence_map import window_map
from .llm import count_tokens
from .memory_ops import parse_lines

_THINK = re.compile(r'<think>.*?</think>\s*', re.S)
STORE = ('ADD', 'SUPERSEDE')


def clean_reply(stage):
    """The single reply of one role, without a think block; None if it was continued, cut off or has bad lines."""
    if stage.get('messages') is None:  # manager not called: the extractor found nothing
        return ''
    if len(stage['replies']) != 1 or not stage['complete']:
        return None
    text = _THINK.sub('', stage['replies'][0]).strip()
    _, complete, bad = parse_lines(text)
    return text if complete and not bad else None


def stored_tokens(row):
    return sum(count_tokens(row['facts'][a['fact'] - 1]['content']) for a in row['actions']
               if a['kind'] in STORE and a.get('fact') and 0 < a['fact'] <= len(row['facts']))


def select(role, rows, rewards, min_r_task=0.0, ablate='none'):
    """(kept row or None, reason). rows: every sample of one window; rewards: sample -> reward row."""
    use_rind, use_revid = ablate != 'no_rind', ablate != 'no_revid'
    rind = lambda r: r['rind']['score'] if use_rind else 0
    revid = lambda r: (rewards[r['sample']]['r_evid'] or 0) if use_revid else 0
    cands = [r for r in rows if (r['rind']['all_pass'] or not use_rind) and clean_reply(r['extract']) is not None
             and clean_reply(r['manage']) is not None]
    if not cands:
        return None, 'no_candidate'
    if role in ('qa', 'evidence'):
        if any(r['sample'] not in rewards for r in cands):
            return None, 'pending'
        if role == 'qa':
            key = lambda r: (rewards[r['sample']]['r_task'], revid(r), rind(r), -stored_tokens(r), -r['sample'])
        else:
            key = lambda r: (revid(r), rind(r), -stored_tokens(r), -r['sample'])
        best = max(cands, key=key)
        if role == 'qa' and rewards[best['sample']]['r_task'] < min_r_task:
            return None, 'below_min_r_task'
        return best, 'best_reward'
    if role == 'noop':
        return min(cands, key=lambda r: (len(r['facts']), r['sample'])), 'fewest_facts'
    return min(cands, key=lambda r: r['sample']), 'backbone' if min(r['sample'] for r in cands) == 0 else 'lowest_sample'


def chat_samples(row, meta):
    out = []
    for writer_role, stage in (('extractor', row['extract']), ('manager', row['manage'])):
        if stage.get('messages') is None:
            continue
        msgs = [dict(m) for m in stage['messages']] + [{'role': 'assistant', 'content': clean_reply(stage)}]
        out.append({'messages': msgs, 'writer_role': writer_role} | meta)
    return out


def is_val(history_id, window, share):
    h = int(hashlib.md5(f'{history_id}:{window}'.encode()).hexdigest(), 16)
    return (h % 10_000) / 10_000 < share


def pct(xs):
    return {p: int(np.percentile(xs, p)) for p in (50, 90, 99, 100)} if xs else {}


def build(a):
    run = Path(a.out) / a.run_name
    cfg_run = json.loads((run / 'config.json').read_text())
    cfg = cfg_run['method_config']
    histories, questions, gold = load('en', cfg_run['pool'], cfg_run['K'])
    rewards = defaultdict(dict)
    if (run / 'rewards.jsonl').exists():
        for r in read_jsonl(run / 'rewards.jsonl'):
            if r['answerer'] == a.answerer:
                rewards[(r['history_id'], r['window'])][r['sample']] = r

    out = run / ('sft' if a.ablate == 'none' else f'sft_{a.ablate}')
    out.mkdir(exist_ok=True)
    splits, selection = {'train': [], 'val': []}, []
    reasons, kept_roles = Counter(), Counter()
    actions_all, actions_kept = Counter(), Counter()
    for h in histories:
        f = run / 'samples' / f'{h.history_id}.jsonl'
        if not f.exists():
            continue
        wmap = {r['window']: r for r in window_map(h, questions, gold.low_density[h.history_id],
                                                   int(cfg.get('window_size', 16)), int(cfg.get('overlap', 2)))[0]}
        by_window = defaultdict(list)
        for r in read_jsonl(f):
            by_window[r['window']].append(r)
            actions_all.update(x['kind'] for x in r['actions'])
        for j, rows in sorted(by_window.items()):
            role = wmap[j]['role']
            rw = rewards.get((h.history_id, j), {})
            kept, reason = select(role, rows, rw, a.min_r_task, a.ablate)
            reasons[f'{role}:{reason}'] += 1
            sel = {'history_id': h.history_id, 'window': j, 'role': role, 'samples': len(rows),
                   'all_pass': sum(r['rind']['all_pass'] for r in rows), 'reason': reason,
                   'kept': kept['sample'] if kept else None,
                   'r_task': {s: x.get('r_task') for s, x in sorted(rw.items())}}
            selection.append(sel)
            if not kept:
                continue
            kept_roles[role] += 1
            actions_kept.update(x['kind'] for x in kept['actions'])
            r = rw.get(kept['sample'], {})
            meta = {'history_id': h.history_id, 'window': j, 'sample': kept['sample'], 'window_role': role,
                    'r_task': r.get('r_task'), 'r_evid': r.get('r_evid'), 'r_ind': kept['rind']['score'],
                    'n_facts': len(kept['facts'])}
            splits['val' if is_val(h.history_id, j, a.val_share) else 'train'] += chat_samples(kept, meta)

    for name, rows in splits.items():
        (out / f'{name}.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
    (out / 'selection.jsonl').write_text(''.join(json.dumps(s) + '\n' for s in selection))
    lengths = defaultdict(lambda: {'prompt': [], 'reply': []})
    for r in splits['train'] + splits['val']:
        m = r['messages']
        lengths[r['writer_role']]['prompt'].append(sum(count_tokens(x['content']) for x in m[:-1]))
        lengths[r['writer_role']]['reply'].append(count_tokens(m[-1]['content']))
    qa = [s for s in selection if s['role'] == 'qa' and s['kept'] is not None]
    stats = {
        'run': a.run_name, 'ablate': a.ablate, 'answerer': a.answerer, 'min_r_task': a.min_r_task, 'val_share': a.val_share,
        'windows': len(selection), 'kept_windows': sum(kept_roles.values()), 'kept_by_role': dict(kept_roles),
        'reasons': dict(sorted(reasons.items())),
        'samples': {n: dict(Counter(r['writer_role'] for r in rows)) for n, rows in splits.items()},
        'kept_qa_r_task_mean': float(np.mean([s['r_task'][s['kept']] for s in qa])) if qa else None,
        'qa_r_task_mean_all_samples': float(np.mean([v for s in selection if s['role'] == 'qa'
                                                     for v in s['r_task'].values() if v is not None] or [np.nan])),
        'actions_all_samples': dict(actions_all), 'actions_kept': dict(actions_kept),
        'tokens_tiktoken': {k: {p: pct(v[p]) for p in v} for k, v in lengths.items()},
    }
    (out / 'stats.json').write_text(json.dumps(stats, indent=2))
    print(json.dumps({k: stats[k] for k in ('windows', 'kept_windows', 'reasons', 'samples', 'kept_qa_r_task_mean',
                                            'qa_r_task_mean_all_samples', 'actions_kept', 'tokens_tiktoken')}, indent=1))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--run-name', required=True, help='a memeval.teacher run (samples/ + rewards.jsonl)')
    p.add_argument('--answerer', default='luna', choices=['luna', 'qwen8b'], help='whose r_task ranks qa windows')
    p.add_argument('--min-r-task', type=float, default=0.0)
    p.add_argument('--val-share', type=float, default=0.05)
    p.add_argument('--ablate', default='none', choices=['none', 'no_rind', 'no_revid'])
    p.add_argument('--out', default=str(ROOT / 'runs'))
    build(p.parse_args())


if __name__ == '__main__':
    main()
