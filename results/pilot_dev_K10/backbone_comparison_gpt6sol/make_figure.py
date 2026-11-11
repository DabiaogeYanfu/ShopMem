"""Pareto figure: full_context answered by stronger backbones (API gpt-6-sol, local
Qwen3-32B) against the existing dev-K10 pilot methods (all Qwen3-8B). Kept separate
from memeval.figures.pareto because that function colours/labels strictly by method
name (config['method']) with no backbone dimension -- injecting extra 'full_context'
points there would collide visually with the existing Qwen3-8B one. Style constants
copied from memeval/figures.py to match the paper's other figures.

    ~/envs/memeval/bin/python make_figure.py   (run from this directory)

Writes pareto_backbone.pdf/.png/.csv/.meta.json alongside this script.
"""
import csv
import json
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[3]  # M-locomo/
sys.path.insert(0, str(ROOT))
from memeval.figures import style, SURFACE, INK, INK_2, CATEGORICAL, NEUTRAL, MARKERS, LABELS, SINGLE  # noqa: E402

RUNS_QWEN = ['pilot_full_context_K10', 'pilot_rag_hybrid_K10', 'pilot_rag_dense_K10', 'pilot_rag_bm25_K10',
             'pilot_simplemem_K10']
# (run, backbone label, colour) -- highlighted points for stronger-backbone full_context
STRONG_BACKBONES = [
    ('pilot_full_context_qwen32b_K10', 'Qwen3-32B, local', CATEGORICAL[2]),
    ('pilot_full_context_gpt6sol_K10', 'gpt-6-sol, API', CATEGORICAL[0]),
]
OUT = Path(__file__).parent / 'pareto_backbone'


def load(run):
    return json.loads((ROOT / 'runs' / run / 'summary.json').read_text())


def main():
    style()
    fig, ax = plt.subplots(figsize=(SINGLE, 2.6))
    rows = []

    for i, run in enumerate(RUNS_QWEN):
        s = load(run)
        m, x, y = s['method'], s['context_tokens_mean'], 100 * (s['judge_acc_macro'] or 0)
        ax.scatter(x, y, s=26, marker=MARKERS[1 + i % 8], color=NEUTRAL, edgecolor=SURFACE,
                   linewidth=1.2, zorder=3)
        ax.annotate(f"{LABELS.get(m, m)} (Qwen3-8B)", (x, y), xytext=(6, 3), textcoords='offset points',
                    fontsize=7, color=INK_2)
        rows.append({'label': f'{m} (Qwen3-8B)', 'run': run, 'context_tokens_mean': round(x, 1),
                     'judge_acc_macro': round(y, 2)})

    for run, backbone_label, color in STRONG_BACKBONES:
        s = load(run)
        m, x, y = s['method'], s['context_tokens_mean'], 100 * (s['judge_acc_macro'] or 0)
        ax.scatter(x, y, s=48, marker='o', color=color, edgecolor=SURFACE, linewidth=1.2, zorder=4)
        ax.annotate(f"{LABELS.get(m, m)} ({backbone_label})", (x, y), xytext=(6, 3), textcoords='offset points',
                    fontsize=7, color=INK, fontweight='bold')
        rows.append({'label': f'{m} ({backbone_label})', 'run': run, 'context_tokens_mean': round(x, 1),
                     'judge_acc_macro': round(y, 2)})

    ax.set_xscale('log')
    ax.set_xlabel('Context tokens per question (log scale)')
    ax.set_ylabel('LLM-judge accuracy (%)')
    ax.set_ylim(0, max(r['judge_acc_macro'] for r in rows) * 1.15 + 2)
    ax.set_title('Dev K=10 pilot: stronger backbones vs. memory methods (Qwen3-8B)', loc='left', color=INK, fontsize=7.5)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT.with_suffix('.pdf'), bbox_inches='tight')
    fig.savefig(OUT.with_suffix('.png'), dpi=300, bbox_inches='tight')
    with open(OUT.with_suffix('.csv'), 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['label', 'run', 'context_tokens_mean', 'judge_acc_macro'])
        w.writeheader()
        w.writerows(rows)
    OUT.with_suffix('.meta.json').write_text(json.dumps(
        {'command': 'python make_figure.py', 'runs': RUNS_QWEN + [r for r, _, _ in STRONG_BACKBONES]}, indent=2))
    plt.close(fig)
    print(f'wrote {OUT}.pdf/.png/.csv/.meta.json')


if __name__ == '__main__':
    main()
