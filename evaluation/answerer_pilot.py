"""Phase-1 pilot: does the r_task answerer (Qwen3-8B vs gpt-6-luna) rank teacher writes the same way?

Reads runs/<run>/rewards.jsonl rows scored by both answerers (memeval.teacher score) on the same
sampled writes and reports:
  per question   how often the two verdicts agree (and Cohen's kappa), each answerer's accuracy
  per pair       for two samples of the same window: concordant (both order them the same way),
                 discordant (opposite ways), one-sided (only one answerer separates them); pairs
                 neither separates are counted apart
  best sample    windows where the two answerers' best-scoring sample sets overlap (what SFT
                 filtering selects)
Decision rule (v2 方法论, 已定的实现细节): consistent rankings -> r_task uses 8B.

    python evaluation/answerer_pilot.py --run runs/teacher_train_K20 --out results/answerer_pilot
writes <out>/answerer_pilot.{pdf,png,csv,meta.json} and summary.json.
"""
import argparse
import csv
import json
import sys
from collections import Counter
from itertools import combinations
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memeval.data import read_jsonl  # noqa: E402
from memeval.figures import CATEGORICAL, GRID, INK, NEUTRAL, SURFACE, style  # noqa: E402

A, B = 'qwen8b', 'luna'
LABEL = {A: 'Qwen3-8B', B: 'gpt-6-luna'}


def analyse(rows):
    by = {}
    for r in rows:
        if r.get('r_task') is not None:
            by.setdefault((r['history_id'], r['window'], r['sample']), {})[r['answerer']] = r
    paired = {k: v for k, v in by.items() if A in v and B in v}
    # per question
    qa = []
    for k, v in paired.items():
        la = {q['question_id']: q['correct'] for q in v[A]['questions']}
        for q in v[B]['questions']:
            qa.append((la[q['question_id']], q['correct']))
    agree = sum(x == y for x, y in qa) / len(qa)
    pa, pb = sum(x for x, _ in qa) / len(qa), sum(y for _, y in qa) / len(qa)
    pe = pa * pb + (1 - pa) * (1 - pb)
    kappa = (agree - pe) / (1 - pe) if pe < 1 else None
    # per window pairs and best samples
    windows = {}
    for (h, w, s), v in paired.items():
        windows.setdefault((h, w), {})[s] = (v[A]['r_task'], v[B]['r_task'], len(v[A]['questions']))
    pairs, best = Counter(), []
    for ws in windows.values():
        for (_, (a1, b1, _)), (_, (a2, b2, _)) in combinations(sorted(ws.items()), 2):
            da, db = np.sign(a1 - a2), np.sign(b1 - b2)
            pairs['tie_both' if da == db == 0 else 'one_sided' if da == 0 or db == 0 else
                  'concordant' if da == db else 'discordant'] += 1
        ma, mb = max(x[0] for x in ws.values()), max(x[1] for x in ws.values())
        top_a = {s for s, x in ws.items() if x[0] == ma}
        top_b = {s for s, x in ws.items() if x[1] == mb}
        best.append({'overlap': bool(top_a & top_b), 'all_tied_a': len(top_a) == len(ws),
                     'all_tied_b': len(top_b) == len(ws), 'n_questions': next(iter(ws.values()))[2]})
    sep = pairs['concordant'] + pairs['discordant'] + pairs['one_sided']
    informative = [b for b in best if not (b['all_tied_a'] and b['all_tied_b'])]
    summary = {
        'sampled_writes': len(paired), 'windows': len(windows), 'question_verdicts': len(qa),
        'accuracy': {A: pa, B: pb}, 'verdict_agreement': agree, 'cohen_kappa': kappa,
        'pairs': dict(pairs), 'pairs_separated': sep,
        'concordant_share': pairs['concordant'] / sep if sep else None,
        'discordant_share': pairs['discordant'] / sep if sep else None,
        'best_overlap_all_windows': sum(b['overlap'] for b in best) / len(best),
        'best_overlap_informative_windows': (sum(b['overlap'] for b in informative) / len(informative)
                                             if informative else None),
        'informative_windows': len(informative)}
    return summary, paired, pairs


def figure(summary, paired, pairs, out):
    style()
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(7.0, 2.8), gridspec_kw={'width_ratios': [1, 1.25], 'wspace': 0.3})
    rng = np.random.default_rng(0)
    xs = np.array([v[A]['r_task'] for v in paired.values()])
    ys = np.array([v[B]['r_task'] for v in paired.values()])
    jit = lambda n: rng.uniform(-0.02, 0.02, n)
    ax1.plot([0, 1], [0, 1], color=NEUTRAL, lw=0.8, ls='--', zorder=1)
    ax1.scatter(xs + jit(len(xs)), ys + jit(len(ys)), s=14, color=CATEGORICAL[0], alpha=0.55,
                edgecolor=SURFACE, linewidth=0.5, zorder=3)
    ax1.set_xlim(-0.05, 1.05), ax1.set_ylim(-0.05, 1.05)
    ax1.set_xlabel(f'r_task, {LABEL[A]} answers'), ax1.set_ylabel(f'r_task, {LABEL[B]} answers')
    ax1.set_title(f'(a) r_task of the same {len(paired)} sampled writes', loc='left', color=INK)
    ax1.text(0.98, 0.04, f'per-question verdict agreement {summary["verdict_agreement"]:.0%}',
             transform=ax1.transAxes, ha='right', va='bottom', fontsize=7, color=INK)
    kinds = [('concordant', 'Same order', CATEGORICAL[0]), ('one_sided', 'Only one separates', NEUTRAL),
             ('discordant', 'Opposite order', CATEGORICAL[1])]
    sep = summary['pairs_separated']
    left = 0
    for key, label, color in kinds:
        share = pairs[key] / sep if sep else 0
        ax2.barh([0], [share], left=left, color=color, edgecolor=SURFACE, linewidth=2, height=0.6)
        if share > 0.06:
            ax2.text(left + share / 2, 0, f'{share:.0%}', ha='center', va='center', color='white', fontsize=7)
        left += share
    ax2.set_xlim(0, 1), ax2.set_ylim(-1.2, 1.0), ax2.set_yticks([]), ax2.grid(False)
    ax2.set_xlabel(f'Share of {sep} sample pairs that at least one answerer separates')
    ax2.set_title('(b) Do the answerers order two samples alike?', loc='left', color=INK)
    ax2.legend(handles=[matplotlib.patches.Patch(color=c, label=l) for _, l, c in kinds], loc='lower center',
               bbox_to_anchor=(0.5, 0.0), ncol=3, frameon=False)
    for s in ('left', 'bottom'):
        ax2.spines[s].set_color(GRID)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    stem = out / 'answerer_pilot'
    fig.savefig(stem.with_suffix('.pdf'), bbox_inches='tight')
    fig.savefig(stem.with_suffix('.png'), dpi=300, bbox_inches='tight')
    plt.close(fig)
    with open(stem.with_suffix('.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['history_id', 'window', 'sample', f'r_task_{A}', f'r_task_{B}', 'n_questions'])
        for (h, win, s), v in sorted(paired.items()):
            w.writerow([h, win, s, v[A]['r_task'], v[B]['r_task'], len(v[A]['questions'])])


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--run', required=True)
    p.add_argument('--out', required=True)
    a = p.parse_args()
    summary, paired, pairs = analyse(read_jsonl(Path(a.run) / 'rewards.jsonl'))
    figure(summary, paired, pairs, a.out)
    out = Path(a.out)
    (out / 'summary.json').write_text(json.dumps(summary, indent=2))
    (out / 'answerer_pilot.meta.json').write_text(json.dumps(
        {'command': 'python ' + ' '.join(sys.argv), 'run': a.run}, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
