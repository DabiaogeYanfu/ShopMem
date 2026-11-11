"""Chapter-4 figures for the JMS manuscript, style 2 (user-chosen look, 2026-11-02).

    python -m memeval.paper_figs_jms2 grpo waterfall kcurve ...   # or: all

Style: one colour theme, matplotlib's PuBu (ShopMem = darkest; others lighter steps of the same ramp);
Times-like serif text (STIX); no grid; left/bottom spines only; one shared legend centred above the
panels; panel tags "(a)" centred under each panel; training curves drawn TensorBoard-style (faint raw
values, bold EMA, light band). Outputs go to results/paper_jms_v2/ through memeval.figures.save
(pdf + png + csv + meta.json). Data sources are the same frozen runs as memeval.paper_figs_jms.
"""
import json
import sys
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import to_hex

from memeval.figures import save

OUT = Path('results/paper_jms_v2')
RUNS = Path('runs')
PUBU = plt.get_cmap('PuBu')


def pubu(x):
    return to_hex(PUBU(x))


C1, C2, C3, C4 = pubu(0.95), pubu(0.68), pubu(0.48), pubu(0.30)   # dark -> light
INK = '#1a1a1a'
DOUBLE, SINGLE = 7.16, 3.5


def style():
    plt.rcParams.update({
        'font.family': 'serif', 'font.serif': ['STIXGeneral', 'DejaVu Serif'], 'mathtext.fontset': 'stix',
        'font.size': 9, 'axes.labelsize': 9, 'xtick.labelsize': 8, 'ytick.labelsize': 8, 'legend.fontsize': 8.5,
        'legend.frameon': False, 'axes.spines.top': False, 'axes.spines.right': False, 'axes.grid': False,
        'xtick.direction': 'out', 'ytick.direction': 'out', 'axes.linewidth': 0.8,
        'xtick.major.width': 0.8, 'ytick.major.width': 0.8, 'axes.edgecolor': INK, 'axes.labelcolor': INK,
        'xtick.color': INK, 'ytick.color': INK, 'text.color': INK,
        'figure.facecolor': 'white', 'axes.facecolor': 'white', 'savefig.facecolor': 'white',
        'lines.linewidth': 1.6, 'pdf.fonttype': 42, 'ps.fonttype': 42,
    })


def xlabel_tag(ax, label, tag):
    """x-axis label with the panel tag centred on a line below it, as in the reference figures."""
    ax.set_xlabel(f'{label}\n({tag})', linespacing=1.9)


def summary(run):
    return json.loads((RUNS / run / 'summary.json').read_text())


def ema(xs, a=0.6):
    out, m = [], None
    for x in xs:
        m = x if m is None else a * m + (1 - a) * x
        out.append(m)
    return np.array(out)


def band(raw, smooth, w=5):
    """Half-width of the light band: rolling std of the raw values around the EMA."""
    r = np.asarray(raw) - smooth
    return np.array([r[max(0, i - w + 1):i + 1].std() if i else 0.0 for i in range(len(r))])


# ------------------------------------------------------------------------------------------- GRPO
GRPO_RUNS = [('reader_grpo_stab1', 'Stabilised GRPO (ours)', C1), ('reader_grpo_lr1e5', 'Standard GRPO', C3)]
GRPO_PANELS = [('reward', 'Training reward'), ('entropy', 'Policy entropy'),
               ('kl_per_token', 'KL to reference (per token)'), ('dev', 'Dev30 accuracy (%)')]


def dev_points(run):
    tag = run.replace('reader_', '', 1)
    pts = [(0, 100 * summary('dev30_gate1_reader_sft_sol')['judge_acc_macro'])]
    for d in sorted(RUNS.glob(f'dev30_{tag}_step*'), key=lambda d: int(d.name.rsplit('step', 1)[1])):
        if (d / 'summary.json').exists():
            pts.append((int(d.name.rsplit('step', 1)[1]), 100 * summary(d.name)['judge_acc_macro']))
    final = RUNS / f'dev30_{tag}_final'
    if (final / 'summary.json').exists():
        log = [json.loads(line) for line in open(RUNS / run / 'log.jsonl') if line.strip()]
        last = max(e['step'] for e in log if 'entropy' in e)
        if last not in dict(pts):
            pts.append((last, 100 * summary(final.name)['judge_acc_macro']))
    return pts


def grpo():
    style()
    fig, axes = plt.subplots(2, 2, figsize=(DOUBLE, 4.9))
    rows = []
    sft = 100 * summary('dev30_gate1_reader_sft_sol')['judge_acc_macro']
    for k, (ax, (key, ylabel)) in enumerate(zip(axes.flat, GRPO_PANELS)):
        for run, lab, c in GRPO_RUNS:
            if key == 'dev':
                pts = dev_points(run)
                ax.plot([p[0] for p in pts], [p[1] for p in pts], color=c, marker='o', markersize=4.2,
                        markeredgecolor='white', markeredgewidth=0.7, label=lab, zorder=3)
                rows += [{'run': run, 'metric': 'dev30_acc', 'step': s, 'raw': round(v, 2), 'ema': ''} for s, v in pts]
                continue
            log = [e for e in (json.loads(line) for line in open(RUNS / run / 'log.jsonl') if line.strip())
                   if 'entropy' in e]
            steps = np.array([e['step'] for e in log])
            raw = np.array([e[key] for e in log], dtype=float)
            sm = ema(raw)
            bw = band(raw, sm)
            ax.plot(steps, raw, color=c, alpha=0.28, linewidth=0.9, zorder=2)
            ax.fill_between(steps, sm - bw, sm + bw, color=c, alpha=0.13, linewidth=0, zorder=1)
            ax.plot(steps, sm, color=c, label=lab, zorder=3)
            rows += [{'run': run, 'metric': key, 'step': int(s), 'raw': round(r, 5), 'ema': round(m, 5)}
                     for s, r, m in zip(steps, raw, sm)]
        if key == 'dev':
            ax.axhline(sft, color=C2, linestyle='--', linewidth=1.0, zorder=1, label='SFT Reader (start)')
        ax.axvline(20, color=INK, linestyle=':', linewidth=1.0, zorder=0)
        ax.set_ylabel(ylabel)
        ax.set_xlim(0, 70)
        xlabel_tag(ax, 'GRPO step', 'abcd'[k])
    axes.flat[1].annotate('standard GRPO\nbest checkpoint', (20, axes.flat[1].get_ylim()[1]), xytext=(4, -4),
                          textcoords='offset points', fontsize=7.5, va='top', color=INK)
    handles = [plt.Line2D([], [], color=c, linewidth=1.8) for _, _, c in GRPO_RUNS] + \
              [plt.Line2D([], [], color=C2, linestyle='--', linewidth=1.0)]
    fig.legend(handles, [lab for _, lab, _ in GRPO_RUNS] + ['SFT Reader (start of GRPO)'], ncol=3,
               loc='upper center', bbox_to_anchor=(0.5, 1.0))
    fig.tight_layout(rect=(0, 0, 1, 0.95), h_pad=1.2, w_pad=2.5)
    save(fig, OUT / 'grpo_training', rows, ['run', 'metric', 'step', 'raw', 'ema'],
         [str(RUNS / r) for r, _, _ in GRPO_RUNS])


# -------------------------------------------------------------------------------------- waterfall
STEPS = [('v2test90_K30_writer_sft_ep3_req', 'One-shot\nanswer'),
         ('v2test90_K30_tmax0_reader_sft_sol', '+ Reader\n($T_{\\max}=0$)'),
         ('v2test90_K30_reader_sft_sol', '+ supplementary\nretrieval'),
         ('v2test90_K30_grpo_stab1_final', '+ GRPO')]


def waterfall():
    style()
    fig, ax = plt.subplots(figsize=(SINGLE, 2.7))
    acc = [round(100 * summary(r)['judge_acc_micro'], 1) for r, _ in STEPS]   # as printed in the tables
    rows, prev = [], 0.0
    shades = [C4, C3, C2, C1]
    for i, ((run, _), a) in enumerate(zip(STEPS, acc)):
        if i == 0:
            ax.bar(i, a, 0.6, color=pubu(0.18), edgecolor=C3, linewidth=0.8, zorder=2)
            ax.text(i, a + 1.6, f'{a:.1f}', ha='center', fontsize=8.5)
        else:
            ax.bar(i, prev, 0.6, color=pubu(0.12), zorder=1)
            ax.bar(i, a - prev, 0.6, bottom=prev, color=shades[i], zorder=2)
            ax.text(i, a + 1.6, f'+{a - prev:.1f}', ha='center', fontsize=8.5)
            ax.plot([i - 1 + 0.3, i - 0.3], [prev, prev], color=INK, linewidth=0.6, linestyle=':')
        rows.append({'run': run, 'judge_acc': a, 'increment': round(a - prev, 1)})
        prev = a
    ax.annotate(f'{acc[-1]:.1f}', (3.3, acc[-1]), xytext=(2, 0), textcoords='offset points', va='center',
                fontsize=8.5, fontweight='bold', color=C1)
    ax.set_xticks(range(len(STEPS)), [lab for _, lab in STEPS], fontsize=7.8)
    ax.set_ylim(0, 100)
    ax.set_ylabel('Judge accuracy on test90 (%)')
    fig.tight_layout()
    save(fig, OUT / 'reader_waterfall', rows, ['run', 'judge_acc', 'increment'], [str(RUNS / r) for r, _ in STEPS])


# ----------------------------------------------------------------------------------------- K curve
K_SERIES = [('grpo_stab1_final', 'ShopMem', C1, 'o'), ('rag_hybrid_rr', 'RAG (hybrid + rerank)', C2, 's'),
            ('full_context_plain', 'Full context', C3, '^')]


def kcurve():
    style()
    Ks = [1, 10, 30, 90]
    panels = [('judge_acc_micro', 'Judge accuracy (%)', 100), ('turn_recall', 'Evidence recall (%)', 100),
              ('context_tokens_mean', 'Context tokens per question', 1)]
    fig, axes = plt.subplots(1, 3, figsize=(DOUBLE, 2.55))
    rows = []
    for k, (ax, (key, ylabel, scale)) in enumerate(zip(axes, panels)):
        for tag, lab, c, m in K_SERIES:
            ys = [summary(f'v2test90_K{K}_{tag}')[key] * scale for K in Ks]
            ax.plot(Ks, ys, color=c, marker=m, markersize=5, markeredgecolor='white', markeredgewidth=0.7,
                    zorder=3)
            if tag == 'full_context_plain':   # truncated to the 100k window at K = 30, 90
                ax.plot(Ks[2:], ys[2:], linestyle='none', marker=m, markersize=5, markerfacecolor='white',
                        markeredgecolor=c, markeredgewidth=1.1, zorder=4)
            rows += [{'metric': key, 'series': lab, 'K': K, 'value': round(y, 2)} for K, y in zip(Ks, ys)]
        ax.set_xscale('log')
        ax.set_xticks(Ks, [str(K) for K in Ks])
        ax.minorticks_off()
        ax.set_xlim(0.75, 120)
        if key == 'context_tokens_mean':
            ax.set_yscale('log')
        else:
            ax.set_ylim(0, 100)
        ax.set_ylabel(ylabel)
        xlabel_tag(ax, 'Scenarios per history $K$', 'abc'[k])
    handles = [plt.Line2D([], [], color=c, marker=m, markersize=5, markeredgecolor='white') for _, _, c, m in K_SERIES]
    handles.append(plt.Line2D([], [], linestyle='none', marker='^', markersize=5, markerfacecolor='white',
                              markeredgecolor=C3))
    fig.legend(handles, [lab for _, lab, _, _ in K_SERIES] + ['Truncated to 100k tokens'], ncol=4,
               loc='upper center', bbox_to_anchor=(0.5, 1.02))
    fig.tight_layout(rect=(0, 0, 1, 0.92), w_pad=2.5)
    save(fig, OUT / 'history_length', rows, ['metric', 'series', 'K', 'value'],
         [str(RUNS / f'v2test90_K{K}_{t}') for K in Ks for t, *_ in K_SERIES])


FIGS = {'grpo': grpo, 'waterfall': waterfall, 'kcurve': kcurve}

if __name__ == '__main__':
    names = sys.argv[1:] or ['all']
    for n in (list(FIGS) if names == ['all'] else names):
        FIGS[n]()
