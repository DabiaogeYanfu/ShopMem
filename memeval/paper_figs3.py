"""Section-4 figures for the manuscript, style 3: figures4papers look in the PuBu theme.

    python -m memeval.paper_figs3 grpo waterfall kcurve retrieval gate growth    # or: all

Look (after github.com/ChenLiu-1996/figures4papers, design-theory.md): drawn at 2x print size (double
column = 14.32 in, single = 7.0 in) with large sans text (Roboto, Apache-2.0, shipped in assets/fonts),
thick black left/bottom spines, no grid, frameless legends, arrows on y labels (higher/lower is better),
grey dashed reference lines, one colour family (matplotlib PuBu: ShopMem darkest, others lighter
steps), same-hue depth for cumulative ablations, hatching for subtypes. Panel tags "(a)" sit under each
panel. Data come from the same frozen runs and verified loaders as memeval.paper_figs(2); outputs go
to results/paper_figs_v3/ through memeval.figures.save (pdf + png + csv + meta.json).
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from matplotlib import font_manager
from matplotlib.collections import LineCollection
from matplotlib.colors import to_hex, to_rgba

from memeval.figures import save
from memeval.paper_figs import (CAT, DEV30, ONE_SESSION, T90, WRITERS, density, flat_turns, gate_rates, jsonl,
                                    render_turn_line, render_unit, retrieval_rates, written_turns)
from memeval.paper_figs2 import GRPO_RUNS, STEPS, band, dev_points, ema, summary

OUT = Path('results/paper_figs_v3')
RUNS = Path('runs')
for f in Path('assets/fonts').glob('Roboto-*.ttf'):
    font_manager.fontManager.addfont(str(f))
PUBU = plt.get_cmap('PuBu')


def pubu(x):
    return to_hex(PUBU(x))


C1, C2, C3, C4, C5 = pubu(0.97), pubu(0.78), pubu(0.6), pubu(0.42), pubu(0.25)   # dark -> light
FAINT = pubu(0.1)
REF = (0, 0, 0, 0.32)          # grey dashed reference line, as "SFT only" in the reference figures
DOUBLE, SINGLE = 14.32, 7.0    # 2x print size
UP, DOWN = r' $\rightarrow$', r' $\leftarrow$'   # read as up/down in rotated y labels


def style():
    plt.rcParams.update({
        'font.family': 'Roboto', 'mathtext.fontset': 'custom', 'mathtext.rm': 'Roboto',
        'mathtext.it': 'Roboto:italic', 'mathtext.bf': 'Roboto:bold', 'mathtext.cal': 'Roboto', 'mathtext.sf': 'Roboto',
        'font.size': 18, 'axes.labelsize': 20, 'xtick.labelsize': 17, 'ytick.labelsize': 17, 'legend.fontsize': 17,
        'legend.frameon': False, 'axes.spines.top': False, 'axes.spines.right': False, 'axes.grid': False,
        'axes.linewidth': 2.5, 'axes.edgecolor': 'black', 'xtick.major.width': 2.0, 'ytick.major.width': 2.0,
        'xtick.major.size': 7, 'ytick.major.size': 7, 'xtick.minor.size': 0, 'ytick.minor.size': 0,
        'axes.labelpad': 10, 'lines.linewidth': 3.2, 'lines.markersize': 10,
        'figure.facecolor': 'white', 'axes.facecolor': 'white', 'savefig.facecolor': 'white',
        'hatch.linewidth': 1.4, 'pdf.fonttype': 42, 'ps.fonttype': 42, 'svg.fonttype': 'none',
    })


def tag(ax, label, t):
    ax.set_xlabel(f'{label}\n({t})', linespacing=1.9)


def fading_line(ax, x, y, color, lw=3.2, a0=0.35, a1=1.0):
    """Line whose alpha rises left to right (figures4papers VIGIL curves)."""
    pts = np.column_stack([x, y])
    seg = np.stack([pts[:-1], pts[1:]], axis=1)
    rgb = to_rgba(color)[:3]
    ax.add_collection(LineCollection(seg, colors=[(*rgb, a) for a in np.linspace(a0, a1, len(seg))],
                                     linewidths=lw, capstyle='round', zorder=3))


# ------------------------------------------------------------------------------------------- GRPO
def grpo():
    style()
    runs = [(r, lab, c) for (r, lab, _), c in zip(GRPO_RUNS, (C1, C4))]
    panels = [('reward', 'Training reward' + UP), ('entropy', 'Policy entropy'),
              ('kl_per_token', 'KL to reference policy'), ('dev', 'Dev30 accuracy (%)' + UP)]
    fig, axes = plt.subplots(2, 2, figsize=(DOUBLE, 10.2))
    sft = 100 * summary('dev30_gate1_reader_sft_sol')['judge_acc_macro']
    rows = []
    for k, (ax, (key, ylabel)) in enumerate(zip(axes.flat, panels)):
        ax.axvline(20, color='black', linestyle=':', linewidth=2.0, alpha=0.6, zorder=0)
        for run, lab, c in runs:
            if key == 'dev':
                pts = dev_points(run)
                ax.plot([p[0] for p in pts], [p[1] for p in pts], color=c, marker='o', markersize=10,
                        markeredgecolor='white', markeredgewidth=1.5, zorder=3)
                rows += [{'run': run, 'metric': 'dev30_acc', 'step': s, 'raw': round(v, 2), 'ema': ''} for s, v in pts]
                continue
            log = [e for e in (json.loads(line) for line in open(RUNS / run / 'log.jsonl') if line.strip())
                   if 'entropy' in e]
            steps = np.array([e['step'] for e in log])
            raw = np.array([e[key] for e in log], dtype=float)
            sm = ema(raw)
            bw = band(raw, sm)
            ax.plot(steps, raw, color=c, alpha=0.25, linewidth=1.6, zorder=2)
            ax.fill_between(steps, sm - bw, sm + bw, color=c, alpha=0.14, linewidth=0, zorder=1)
            ax.plot(steps, sm, color=c, linewidth=3.6, zorder=3)
            rows += [{'run': run, 'metric': key, 'step': int(s), 'raw': round(r, 5), 'ema': round(m, 5)}
                     for s, r, m in zip(steps, raw, sm)]
        if key == 'dev':
            ax.axhline(sft, color=REF, linestyle='--', linewidth=3.5, zorder=1)
        ax.set_xlim(0, 70)
        if key == 'kl_per_token':
            ax.set_yticks([0, 0.05, 0.10, 0.15])
        ax.set_ylabel(ylabel)
        tag(ax, 'GRPO step', 'abcd'[k])
    axes.flat[1].text(21.5, axes.flat[1].get_ylim()[1] * 0.97, 'best checkpoint of\nstandard GRPO', fontsize=15,
                      va='top', color='#333333')
    handles = [plt.Line2D([], [], color=c, linewidth=3.6) for _, _, c in runs] + \
              [plt.Line2D([], [], color=REF, linestyle='--', linewidth=3.5)]
    fig.legend(handles, [lab for _, lab, _ in runs] + ['SFT Reader (start of GRPO)'], ncol=3,
               loc='upper center', bbox_to_anchor=(0.5, 1.0), handlelength=2.6, columnspacing=2.5)
    fig.tight_layout(rect=(0, 0, 1, 0.95), h_pad=2.0, w_pad=4.0)
    save(fig, OUT / 'grpo_training', rows, ['run', 'metric', 'step', 'raw', 'ema'], [str(RUNS / r) for r, _, _ in runs])


# -------------------------------------------------------------------------------------- waterfall
def waterfall():
    style()
    fig, ax = plt.subplots(figsize=(SINGLE, 5.6))
    acc = [round(100 * summary(r)['judge_acc_micro'], 1) for r, _ in STEPS]   # as printed in the tables
    labels = ['One-shot\nanswer', '+ Reader\n($T_{\\max}=0$)', '+ supplem.\nretrieval', '+ GRPO']
    shades = [C5, C3, C2, C1]
    rows, prev = [], 0.0
    for i, a in enumerate(acc):
        if i == 0:
            ax.bar(i, a, 0.62, color=C5, zorder=2)
            ax.text(i, a + 1.5, f'{a:.1f}', ha='center', va='bottom', fontsize=17)
        else:
            ax.bar(i, prev, 0.62, color=FAINT, zorder=1)
            ax.bar(i, a - prev, 0.62, bottom=prev, color=shades[i], zorder=2)
            ax.text(i, a + 1.5, f'+{a - prev:.1f}', ha='center', va='bottom', fontsize=17)
            ax.plot([i - 1 + 0.31, i - 0.31], [prev, prev], color='black', linewidth=1.6, linestyle=':', alpha=0.6)
        rows.append({'run': STEPS[i][0], 'judge_acc': a, 'increment': round(a - prev, 1)})
        prev = a
    ax.text(3, acc[-1] / 2 + acc[-2] / 2 - 1, f'{acc[-1]:.1f}', ha='center', va='center', color='white',
            fontsize=16, fontweight='bold')
    ax.set_xticks(range(4), labels, fontsize=15.5)
    ax.set_ylim(0, 100)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_ylabel('Judge accuracy on test90 (%)' + UP)
    fig.tight_layout(pad=1.2)
    save(fig, OUT / 'reader_waterfall', rows, ['run', 'judge_acc', 'increment'], [str(RUNS / r) for r, _ in STEPS])


# ----------------------------------------------------------------------------------------- K curve
def kcurve():
    style()
    Ks = np.array([1, 10, 30, 90])
    series = [('grpo_stab1_final', 'ShopMem', C1, 'o'), ('rag_hybrid_rr', 'RAG (hybrid + rerank)', C3, 's'),
              ('full_context_plain', 'Full context', C4, '^')]
    panels = [('judge_acc_micro', 'Judge accuracy (%)' + UP, 100), ('turn_recall', 'Evidence recall (%)' + UP, 100),
              ('context_tokens_mean', 'Context tokens' + DOWN, 1)]
    fig, axes = plt.subplots(1, 3, figsize=(DOUBLE, 5.4))
    rows = []
    for k, (ax, (key, ylabel, scale)) in enumerate(zip(axes, panels)):
        logy = key == 'context_tokens_mean'
        for tag_, lab, c, m in series:
            ys = np.array([summary(f'v2test90_K{K}_{tag_}')[key] * scale for K in Ks])
            fading_line(ax, np.log10(Ks), np.log10(ys) if logy else ys, c, a0=0.5)
            hollow = tag_ == 'full_context_plain'
            for i, (K, y) in enumerate(zip(Ks, ys)):
                open_ = hollow and K >= 30   # truncated to the 100k window
                ax.plot(np.log10(K), np.log10(y) if logy else y, marker=m, markersize=11, linestyle='none',
                        color=c, markerfacecolor='white' if open_ else c, markeredgewidth=2.2 if open_ else 0, zorder=4)
            rows += [{'metric': key, 'series': lab, 'K': int(K), 'value': round(y, 2)} for K, y in zip(Ks, ys)]
        ax.set_xticks(np.log10(Ks), [str(K) for K in Ks])
        ax.set_xlim(-0.15, np.log10(90) + 0.15)
        if logy:
            ax.set_yticks([3, 4, 5], ['$10^3$', '$10^4$', '$10^5$'])
            ax.set_ylim(2.75, 5.2)
        else:
            ax.set_ylim(0, 100)
            ax.set_yticks([0, 25, 50, 75, 100])
        ax.set_ylabel(ylabel)
        tag(ax, 'Scenarios per history $K$', 'abc'[k])
    handles = [plt.Line2D([], [], color=c, marker=m, markersize=11, linewidth=3.2) for _, _, c, m in series]
    handles.append(plt.Line2D([], [], linestyle='none', marker='^', markersize=11, markerfacecolor='white',
                              markeredgecolor=C4, markeredgewidth=2.2))
    fig.legend(handles, [lab for _, lab, _, _ in series] + ['Truncated to 100k tokens'], ncol=4,
               loc='upper center', bbox_to_anchor=(0.5, 1.0), columnspacing=2.2)
    fig.tight_layout(rect=(0, 0, 1, 0.9), w_pad=4.0)
    save(fig, OUT / 'history_length', rows, ['metric', 'series', 'K', 'value'],
         [str(RUNS / f'v2test90_K{K}_{t}') for K in Ks for t, *_ in series])


# -------------------------------------------------------------------------------------- retrieval
CURRENT = ['working_memory_snapshot', 'state_tracking']   # questions about the present state


def retrieval():
    """v3 Fig. 5: (a) supplementary-retrieval share, SFT vs GRPO; (b) share retrieving closed versions (eta=1).
    Categories grouped into questions about the current state and all other questions."""
    style()
    g = retrieval_rates('v2test90_K30_grpo_stab1_final')
    s = retrieval_rates('v2test90_K30_reader_sft_sol')
    others = sorted([c for c in g if c not in CURRENT], key=lambda c: -g[c][1])
    cats = CURRENT + others
    # top-down: the current-state group first, then a gap, then the other categories
    y = np.array([len(cats) - 1 - i + (1.0 if i < len(CURRENT) else 0.0) for i in range(len(cats))], dtype=float)
    fig, axes = plt.subplots(1, 2, figsize=(DOUBLE, 8.2), sharey=True)
    h = 0.38
    for k, ax in enumerate(axes):
        idx = 0 if k == 0 else 1
        ax.barh(y + h / 2, [s[c][idx] for c in cats], h, color=C4, zorder=2)
        ax.barh(y - h / 2, [g[c][idx] for c in cats], h, color=C1, zorder=2)
        for yy, c in zip(y, cats):
            v = g[c][idx]
            ax.text(v + 1.2, yy - h / 2, f'{v:.1f}', va='center', fontsize=13.5,
                    fontweight='bold' if c in CURRENT else 'normal')
        ax.set_xlim(0, 112)
        ax.set_xticks([0, 25, 50, 75, 100])
        tag(ax, ['Questions with supplementary retrieval (%)', 'Questions retrieving closed versions, $\\eta=1$ (%)'][k],
            'ab'[k])
        ax.axhline((y[len(CURRENT) - 1] + y[len(CURRENT)]) / 2, color='black', linewidth=1.4, linestyle=':', alpha=0.5)
    axes[0].set_yticks(y, [CAT[c] for c in cats])
    axes[0].tick_params(axis='y', length=0)
    axes[1].tick_params(axis='y', length=0)
    axes[0].text(0.99, y[0] + 0.62, 'Questions about the current state', transform=axes[0].get_yaxis_transform(),
                 ha='right', fontsize=15, fontweight='bold', color=C1)
    axes[0].text(0.99, y[len(CURRENT)] + 0.62, 'Other questions', transform=axes[0].get_yaxis_transform(),
                 ha='right', fontsize=15, fontweight='bold', color=C2)
    fig.legend([plt.Rectangle((0, 0), 1, 1, color=C4), plt.Rectangle((0, 0), 1, 1, color=C1)],
               ['SFT Reader', 'Reader after GRPO (ShopMem)'], ncol=2, loc='upper center', bbox_to_anchor=(0.55, 1.0))
    fig.tight_layout(rect=(0, 0, 1, 0.95), w_pad=3.0)
    rows = [{'category': c, 'sft_supplementary': round(s[c][0], 2), 'grpo_supplementary': round(g[c][0], 2),
             'sft_eta1': round(s[c][1], 2), 'grpo_eta1': round(g[c][1], 2)} for c in cats]
    save(fig, OUT / 'reader_retrieval_behaviour', rows, list(rows[0]),
         [str(RUNS / 'v2test90_K30_grpo_stab1_final'), str(RUNS / 'v2test90_K30_reader_sft_sol')])


# ------------------------------------------------------------------------------------------- gate
def gate():
    """v3 Fig. 6 (a)(b) + new (c): extractor gate on dev30, tool returns on test90, one scenario's turns."""
    style()
    dev = density(DEV30)
    wt = {r: written_turns(RUNS / r / 'trace.jsonl') for r, _ in WRITERS}
    rates = {r: gate_rates(wt[r], dev) for r, _ in WRITERS}
    fig = plt.figure(figsize=(DOUBLE, 10.4))
    gs = fig.add_gridspec(2, 2, height_ratios=[1.3, 1], width_ratios=[1.7, 1], hspace=0.62, wspace=0.32)
    ax = fig.add_subplot(gs[0, 0])
    x = np.arange(len(WRITERS))
    w = 0.36
    hi = [rates[r]['recall'] for r, _ in WRITERS]
    lo = [rates[r]['low_written'] for r, _ in WRITERS]
    ax.bar(x - w / 2, hi, w, color=C1, zorder=2, label='High-density turns written')
    ax.bar(x + w / 2, lo, w, color=C4, zorder=2, label='Low-density turns written')
    for i in range(len(WRITERS)):
        ax.text(x[i] - w / 2, hi[i] + 1.5, f'{hi[i]:.1f}', ha='center', va='bottom', fontsize=14)
        ax.text(x[i] + w / 2, lo[i] + 1.5, f'{lo[i]:.1f}', ha='center', va='bottom', fontsize=14)
    ax.set_xticks(x, [f'{lab}\nprecision {rates[r]["precision"] / 100:.2f}' for r, lab in WRITERS], fontsize=15.5)
    ax.set_ylim(0, 100)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_ylabel('Turns written to memory (%)')
    ax.legend(loc='lower center', bbox_to_anchor=(0.5, 1.02), ncol=2, fontsize=15.5)
    ax.set_xlabel('(a)', labelpad=12)
    # (b)
    ax = fig.add_subplot(gs[0, 1])
    t90 = density(T90)
    banks = {f.stem: {tid for u in jsonl(f) for tid in u['source_turns']}
             for f in (RUNS / 'v2test90_K30_writer_sft_ep3_req/banks').glob('*.jsonl')}
    tool = gate_rates(banks, t90, only={'tool_observation'})
    vals = [tool['recall'], tool['low_written']]
    ax.bar([0, 1], vals, 0.56, color=[C1, C4], zorder=2)
    for i, v in enumerate(vals):
        ax.text(i, v - 2 if v > 90 else v + 1.5, f'{v:.1f}', ha='center', va='top' if v > 90 else 'bottom',
                fontsize=15, color='white' if v > 90 else 'black')
    ax.set_xticks([0, 1], ['High-density\ntool returns', 'Routine\ntool returns'], fontsize=15.5)
    ax.set_ylim(0, 100)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_ylabel('Written to memory (%)')
    ax.set_title('ShopMem-Writer on test90', fontsize=15.5, pad=14)
    ax.set_xlabel('(b)', labelpad=12)
    # (c)
    ax = fig.add_subplot(gs[1, :])
    hid = sorted(dev)[0]
    turns = dev[hid]
    sc = turns[0]['dialog_id'].rsplit('_', 2)[0]
    idx = [i for i, t in enumerate(turns, 1) if t['dialog_id'].startswith(sc + '_')]
    lanes = [('High-density (annotation)', None, C1)] + \
            [(f'Written by {lab.splitlines()[0]}', r, C1 if r == WRITERS[-1][0] else C3) for r, lab in WRITERS]
    for li, (lab, r, col) in enumerate(lanes):
        yy = len(lanes) - 1 - li
        for k, i in enumerate(idx):
            t = turns[i - 1]
            on = (not t['is_low_density']) if r is None else f'T{i:05d}' in wt[r][hid]
            ax.add_patch(plt.Rectangle((k, yy + 0.14), 0.84, 0.72, color=col if on else FAINT, linewidth=0))
    for k in range(1, len(idx)):
        if turns[idx[k] - 1]['dialog_id'].split('_')[3] != turns[idx[k - 1] - 1]['dialog_id'].split('_')[3]:
            ax.axvline(k - 0.08, color='black', linewidth=1.6, linestyle=':', alpha=0.55)
    ax.set_xlim(-0.4, len(idx) + 0.2)
    ax.set_ylim(0, len(lanes))
    ax.set_yticks([len(lanes) - 1 - i + 0.5 for i in range(len(lanes))], [lab for lab, _, _ in lanes], fontsize=15.5)
    ax.tick_params(axis='y', length=0)
    ax.set_xticks([])
    for sp in ('left', 'bottom'):
        ax.spines[sp].set_visible(False)
    ax.set_xlabel(f'Turns of one dev30 scenario, sessions separated by dotted lines\n(c)', linespacing=1.9)
    rows = [{'panel': 'a', 'writer': r, **{k: round(v, 2) for k, v in rates[r].items()}} for r, _ in WRITERS]
    rows.append({'panel': 'b', 'writer': 'v2test90_K30_writer_sft_ep3_req', **{k: round(v, 2) for k, v in tool.items()}})
    save(fig, OUT / 'write_gate', rows, ['panel', 'writer', 'recall', 'low_written', 'precision'],
         [str(RUNS / r) for r, _ in WRITERS] + [str(RUNS / 'v2test90_K30_writer_sft_ep3_req')])


# ----------------------------------------------------------------------------------------- growth
def growth():
    """Memory growth over one test90 history (main-experiment banks; token definitions as the harness).
    No end values are printed: Table 8 (efficiency run) is the reference for exact sizes."""
    from memeval.llm import count_tokens
    style()
    hid = 'H_EN_TEST90_K030_001'
    hist = [h for h in jsonl(T90 / 'K030/histories.jsonl') if h['history_id'] == hid][0]
    turns = flat_turns(hist)
    n = len(turns)
    tb = {f'T{i:05d}': t for i, t in enumerate(turns, 1)}
    raw_add = np.zeros(n + 1)
    i = 0
    for s in hist['sessions']:
        raw_add[i + 1] += count_tokens(f"=== {s['session_id']} | {s['site_id']} | {s['session_start']} ===") + 1
        for t in s['turns']:
            i += 1
            raw_add[i] += count_tokens(render_turn_line(t)) + 1
    raw = np.cumsum(raw_add)[1:]
    wend = {int(w['window']): int(w['turns'][-1][1:])
            for w in jsonl('results/window_map/en_test90_K030.jsonl') if w['history_id'] == hid}
    ours = [u for u in jsonl(RUNS / 'v2test90_K30_writer_sft_ep3_req/banks' / f'{hid}.jsonl') if u['source'] != 'request']
    prompt = jsonl(RUNS / 'mfail2_banks_shopmem_v1/banks' / f'{hid}.jsonl')
    ot, pt, act, clo = (np.zeros(n + 1) for _ in range(4))
    for u in ours:
        k = count_tokens(render_unit(u, tb))
        ot[wend[u['window']]] += k
        act[wend[u['window']]] += 1
        if u.get('closed_window') is not None:
            ot[wend[u['closed_window']]] -= k
            act[wend[u['closed_window']]] -= 1
            clo[wend[u['closed_window']]] += 1
    for u in prompt:
        pt[min(n, max(int(t[1:]) for t in u['source_turns']))] += count_tokens(render_unit(u, tb))
    ot, pt, act, clo = (np.cumsum(a)[1:] for a in (ot, pt, act, clo))
    xs = np.arange(1, n + 1)
    fig, axes = plt.subplots(1, 2, figsize=(DOUBLE, 5.6))
    ax = axes[0]
    ax.plot(xs, raw / 1e3, color=REF, linestyle='--', linewidth=3.2, label='Raw interaction')
    ax.plot(xs, pt / 1e3, color=C4, linewidth=3.4, label='ShopMem-Prompt memory')
    ax.plot(xs, ot / 1e3, color=C1, linewidth=3.4, label='ShopMem memory (valid units)')
    ax.set_ylabel('Cumulative tokens (thousands)')
    ax.set_xlim(0, 1750)
    ax.set_xticks(range(0, 1751, 350))
    ax.set_ylim(0, 160)
    ax.legend(loc='upper left', fontsize=15.5)
    tag(ax, 'Turns processed', 'a')
    ax = axes[1]
    ax.fill_between(xs, 0, act, color=C1, linewidth=0, label='Valid units')
    ax.fill_between(xs, act, act + clo, facecolor='white', edgecolor=C3, hatch='///', linewidth=0,
                    label='Closed units (SUPERSEDE / EXPIRE)')
    ax.plot(xs, act + clo, color=C3, linewidth=2.0)
    ax.set_ylabel('Memory units')
    ax.set_xlim(0, 1750)
    ax.set_xticks(range(0, 1751, 350))
    ax.set_ylim(0, 520)
    ax.legend(loc='upper left', fontsize=15.5)
    tag(ax, 'Turns processed', 'b')
    fig.tight_layout(w_pad=4.0)
    rows = [{'turn': int(x), 'raw_tokens': int(raw[x - 1]), 'prompt_memory_tokens': int(pt[x - 1]),
             'shopmem_memory_tokens': int(ot[x - 1]), 'valid_units': int(act[x - 1]), 'closed_units': int(clo[x - 1])}
            for x in list(range(25, n, 25)) + [n]]
    save(fig, OUT / 'memory_growth', rows, list(rows[0]),
         [str(RUNS / 'v2test90_K30_writer_sft_ep3_req/banks'), str(RUNS / 'mfail2_banks_shopmem_v1/banks')])


FIGS = {'grpo': grpo, 'waterfall': waterfall, 'kcurve': kcurve, 'retrieval': retrieval, 'gate': gate,
        'growth': growth}

if __name__ == '__main__':
    names = sys.argv[1:] or ['all']
    for name in (list(FIGS) if names == ['all'] else names):
        FIGS[name]()
