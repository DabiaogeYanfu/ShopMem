"""Chapter-4 figures for the JMS manuscript (plan: 圖表規劃_JMS_v2.md), one function per figure.

    python -m memeval.paper_figs_jms fig06 [fig07 ...]      # or: all

Every figure goes to results/paper_jms/figNN_<name>.{pdf,png,csv,meta.json} through memeval.figures.save,
with the house style of memeval.figures (ours blue, SimpleMem orange, other baselines grey; 8 pt text).
Numbers come from the frozen test90 tables (results/test90_main_v3), the run summaries and the raw
predictions / banks / traces; nothing here calls a model.
"""
import csv
import json
import os
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

import matplotlib
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm

from memeval.figures import (CATEGORICAL, CATEGORY_LABELS, DOUBLE, GRID, INK, INK_2, INK_3, MARKERS, NEUTRAL,
                             SEQUENTIAL_BLUE, SINGLE, SURFACE, place_labels, save, style)

OUT = Path('results/paper_jms')
RUNS = Path('runs')
MAIN = Path('results/test90_main_v3')
T90 = Path('data/long/en/test90')
DEV30 = Path('data/long/en/dev30')

OURS = CATEGORICAL[0]
OURS_LIGHT = SEQUENTIAL_BLUE[2]
SIMPLEMEM = CATEGORICAL[1]
RED = '#d64545'

DISPLAY = {'ShopMem (Writer + Reader)': 'ShopMem', 'ShopMem-Writer (SFT), one-shot': 'Writer + one-shot',
           'ShopMem v1': 'ShopMem-Prompt', 'RAG hybrid+rerank': 'RAG hybrid+rerank', 'RAG hybrid': 'RAG hybrid',
           'RAG dense': 'RAG dense', 'RAG BM25': 'RAG BM25', 'SimpleMem': 'SimpleMem',
           'Full context (plain)': 'Full context', 'Observation mask': 'Obs. mask',
           'Sliding window': 'Sliding window'}
# evidence of these categories lies in one session (1-3 turns); the others span all 5 sessions (13-15 turns)
ONE_SESSION = ['constraint_recall', 'single_hop_fact', 'state_tracking', 'tool_evidence_recall', 'shift_temporal']
FIVE_SESSIONS = ['failed_intervention', 'temporal_order', 'superseded_fact', 'cross_role_dependency',
                 'working_memory_snapshot', 'conditioned_parameter', 'multi_hop_event']
CATS = ONE_SESSION + FIVE_SESSIONS + ['unanswerable']   # fixed row order for figs 7, 9, 11 (sorted by RAG acc. in-group)
CAT = dict(CATEGORY_LABELS, conditioned_parameter='Cond. parameter', working_memory_snapshot='Working-mem. snapshot',
           state_tracking='State tracking', constraint_recall='Constraint recall', tool_evidence_recall='Tool evidence',
           shift_temporal='Shift inference', superseded_fact='Superseded fact', failed_intervention='Failed interv.',
           cross_role_dependency='Cross-role dep.', multi_hop_event='Multi-hop event', single_hop_fact='Single-hop fact')


def color_of(name):
    if name == 'ShopMem':
        return OURS
    if name in ('Writer + one-shot', 'ShopMem-Prompt'):
        return OURS_LIGHT
    return SIMPLEMEM if name == 'SimpleMem' else NEUTRAL


def summary(run):
    return json.loads((RUNS / run / 'summary.json').read_text())


def jsonl(p):
    return [json.loads(line) for line in open(p) if line.strip()]


def read_csv(p):
    return list(csv.DictReader(open(p)))


def panel_tag(ax, tag):
    ax.set_title(tag, loc='left', color=INK, fontweight='bold')


def flat_turns(history):
    """Turns in history order; T-ids in banks, traces and window maps are T%05d of this 1-based index."""
    return [t for s in history['sessions'] for t in s['turns']]


# --------------------------------------------------------------------------------------------- fig 5
def fig05():
    """M-LoCoMo: (a) one scenario's five sessions as turn strips, (b) 30 scenarios interleaved, (c) evidence size."""
    style()
    hist = jsonl(T90 / 'K030/histories.jsonl')[0]
    qs = [q for q in jsonl(T90 / 'K030/questions.jsonl') if q['history_id'] == hist['history_id']]
    sc = 'SC_GEN_0095'
    evid = Counter(e for q in qs if q['scenario_id'] == sc for e in q['evidence'])
    fig = plt.figure(figsize=(DOUBLE, 4.6))
    gs = fig.add_gridspec(2, 2, height_ratios=[1.15, 1], width_ratios=[1.2, 1], hspace=0.62, wspace=0.62)
    # (a) turn strips
    ax = fig.add_subplot(gs[0, :])
    role = {'human': 'Person', 'user_query': 'Person', 'agent_think': 'Agent', 'agent_response': 'Agent',
            'agent_tool_call': 'Agent', 'tool_call': 'System', 'tool_observation': 'System'}
    hue = {'Person': CATEGORICAL[3], 'Agent': INK_3, 'System': CATEGORICAL[2]}
    sessions = [s for s in hist['sessions'] if s['scenario_id'] == sc]
    rows = []
    width = max(len(s['turns']) for s in sessions)
    for i, s in enumerate(sessions):
        y = len(sessions) - 1 - i
        for j, t in enumerate(s['turns']):
            r = role[t['turn_type']]
            low = t['is_low_density']
            ax.add_patch(plt.Rectangle((j, y + 0.12), 0.9, 0.76, color=hue[r], alpha=0.22 if low else 1.0,
                                       linewidth=0))
            if t['dialog_id'] in evid:
                ax.plot(j + 0.45, y + 0.98, marker='v', markersize=3.2, color=INK, zorder=4)
            if t['turn_type'] == 'tool_observation' and not low:
                ax.text(j + 0.45, y + 0.5, t['speaker_id'].split('_')[1], fontsize=4.3, ha='center', va='center',
                        color='white', rotation=90)
            rows.append({'panel': 'a', 'session': s['session_id'], 'turn': j + 1, 'turn_type': t['turn_type'],
                         'role': r, 'low_density': low, 'evidence_of_n_questions': evid.get(t['dialog_id'], 0)})
    ax.set_xlim(-0.3, width + 0.3)
    ax.set_ylim(-0.1, len(sessions) + 0.25)
    kinds = ['Operator–Agent diagnosis' if any((p if isinstance(p, str) else p.get('role')) == 'industrial_agent'
                                               for p in s['participants']) else 'Person–person discussion'
             for s in sessions]
    ax.set_yticks([len(sessions) - 1 - i + 0.5 for i in range(len(sessions))],
                  [f'S{i + 1}  {kinds[i]}' for i in range(len(sessions))])
    ax.set_xlabel('Turn within session')
    ax.grid(False)
    ax.spines['left'].set_visible(False)
    ax.tick_params(axis='y', length=0)
    handles = [plt.Rectangle((0, 0), 1, 1, color=hue[k]) for k in hue] + \
              [plt.Rectangle((0, 0), 1, 1, color=INK_3, alpha=0.22),
               plt.Line2D([], [], marker='v', linestyle='none', color=INK, markersize=4)]
    ax.legend(handles, ['Person', 'Agent (reasoning, calls, replies)', 'Plant system (EMS/MES/QMS/WMS)',
                        'Low-density turn (pale)', 'Evidence turn'],
              ncol=5, loc='lower right', bbox_to_anchor=(1.0, 1.0), handlelength=1.0, columnspacing=1.0,
              fontsize=6.5)
    ax.text(-0.27, 1.06, '(a)', transform=ax.transAxes, fontweight='bold', color=INK)
    # (b) interleaving
    ax = fig.add_subplot(gs[1, 0])
    turns = flat_turns(hist)
    sid_of = {}
    for s in hist['sessions']:
        for t in s['turns']:
            sid_of[t['dialog_id']] = s['scenario_id']
    order = list(dict.fromkeys(sid_of[t['dialog_id']] for t in turns))
    xs = np.arange(1, len(turns) + 1)
    ys = np.array([order.index(sid_of[t['dialog_id']]) for t in turns])
    hit = np.array([sid_of[t['dialog_id']] == sc for t in turns])
    ax.scatter(xs[~hit], ys[~hit], s=0.6, color=NEUTRAL, linewidths=0, rasterized=True)
    ax.scatter(xs[hit], ys[hit], s=1.6, color=INK, linewidths=0)
    ax.set_xlabel(f'Turn in the interleaved history (K = 30, {len(turns)} turns)')
    ax.set_ylabel('Scenario')
    ax.set_yticks([])
    ax.invert_yaxis()
    ax.annotate('scenario of (a)', (xs[hit][0], ys[hit][0]), xytext=(-60, 4), textcoords='offset points',
                fontsize=6.5, color=INK, ha='right', va='center',
                arrowprops=dict(arrowstyle='-', color=INK_3, lw=0.6))
    ax.grid(False)
    panel_tag(ax, '(b)')
    # (c) evidence turns per question, by category
    ax = fig.add_subplot(gs[1, 1])
    allq = [q for q in jsonl(T90 / 'K030/questions.jsonl') if q['answerable']]
    per = defaultdict(list)
    for q in allq:
        per[q['category']].append(len(q['evidence']))
    cats = ONE_SESSION + FIVE_SESSIONS
    vals = [statistics.mean(per[c]) for c in cats]
    cols = [INK_3 if c in ONE_SESSION else INK for c in cats]
    ax.barh(range(len(cats)), vals, color=cols, height=0.7, edgecolor=SURFACE, linewidth=0.8)
    ax.set_yticks(range(len(cats)), [CAT[c] for c in cats])
    ax.invert_yaxis()
    ax.set_xlabel('Evidence turns per question')
    ax.grid(axis='y', visible=False)
    ax.set_xlim(0, 24)
    ax.text(4.0, 2, 'one session', fontsize=6.5, color=INK_2, va='center')
    ax.text(16.0, 8, 'all 5\nsessions', fontsize=6.5, color=INK, va='center')
    panel_tag(ax, '(c)')
    rows += [{'panel': 'c', 'category': c, 'evidence_turns_mean': round(v, 2)} for c, v in zip(cats, vals)]
    low = sum(t['is_low_density'] for t in turns) / len(turns)
    rows.append({'panel': 'note', 'category': 'low_density_share_K030_001', 'evidence_turns_mean': round(100 * low, 1)})
    save(fig, OUT / 'fig05_dataset', rows, sorted({k for r in rows for k in r}, key=str),
         [str(T90 / 'K030')])


# --------------------------------------------------------------------------------------------- fig 6
def main_rows():
    rows = []
    for r in read_csv(MAIN / 'main.csv'):
        s = summary(r['run'])
        rows.append({'label': DISPLAY[r['method']], 'run': r['run'], 'acc': float(r['judge_acc']),
                     'context_tokens': float(r['context_tokens']), 'evidence_recall': 100 * s['turn_recall']})
    return rows


LABEL_OFFSETS = [
    {'ShopMem': (7, 0), 'Writer + one-shot': (6, 4), 'ShopMem-Prompt': (6, -5), 'RAG hybrid+rerank': (-6, 3),
     'RAG hybrid': (-6, -2), 'RAG dense': (-6, 2), 'RAG BM25': (-6, -5), 'SimpleMem': (6, 0),
     'Full context': (0, -9), 'Obs. mask': (-6, 6), 'Sliding window': (6, 0)},
    {'ShopMem': (7, 0), 'Writer + one-shot': (-6, 3), 'ShopMem-Prompt': (6, -3), 'RAG hybrid+rerank': (-6, 3),
     'RAG hybrid': (6, -6), 'RAG dense': (-6, 3), 'RAG BM25': (-6, -5), 'SimpleMem': (6, -2),
     'Full context': (6, -3), 'Obs. mask': (0, 8), 'Sliding window': (6, 0)}]


def fig06():
    style()
    rows = main_rows()
    fig, axes = plt.subplots(1, 2, figsize=(DOUBLE, 2.7))
    for k, (ax, key, xlabel) in enumerate(zip(axes, ['context_tokens', 'evidence_recall'],
                                             ['Context tokens per question (log scale)',
                                              'Evidence recall in the answer context (%)'])):
        grey = 0
        pts = []
        for r in rows:
            c = color_of(r['label'])
            hl = c != NEUTRAL
            m = 'o' if hl else MARKERS[1 + grey % 8]
            grey += not hl
            ax.scatter(r[key], r['acc'], s=46 if r['label'] == 'ShopMem' else 30, marker=m, color=c,
                       edgecolor=SURFACE, linewidth=1.0, zorder=3)
            pts.append((r[key], r['acc'], r['label'], INK if hl else INK_2))
        if key == 'context_tokens':
            ax.set_xscale('log')
            ax.set_xlim(120, 3e5)
        else:
            ax.set_xlim(-5, 112)
        ax.set_ylim(0, 100)
        ax.set_xlabel(xlabel)
        ax.set_ylabel('LLM-judge accuracy (%)')
        for x, y, text, col in pts:
            dx, dy = LABEL_OFFSETS[k].get(text, (6, 0))
            ax.annotate(text, (x, y), xytext=(dx, dy), textcoords='offset points', fontsize=6.5, color=col,
                        ha='left' if dx > 0 else 'right' if dx < 0 else 'center', va='center')
        panel_tag(ax, '(a)' if k == 0 else '(b)')
    fig.tight_layout()
    save(fig, OUT / 'fig06_acc_tokens_recall', rows, ['label', 'run', 'acc', 'context_tokens', 'evidence_recall'],
         [str(MAIN / 'main.csv')] + [str(RUNS / r['run']) for r in rows])


# --------------------------------------------------------------------------------------------- fig 7
FIG7_METHODS = ['ShopMem', 'Writer + one-shot', 'ShopMem-Prompt', 'RAG hybrid+rerank', 'SimpleMem', 'Full context']


def category_table(path):
    t = defaultdict(dict)
    for r in read_csv(path):
        t[r['method']][r['category']] = float(r['judge_acc'])
    return t


def group_bands(ax, n_cols, x0=-0.5):
    """Thin rules between the one-session, five-session and unanswerable row blocks."""
    for y in (len(ONE_SESSION) - 0.5, len(ONE_SESSION) + len(FIVE_SESSIONS) - 0.5):
        ax.axhline(y, color=INK, linewidth=0.9)


def fig07():
    style()
    t = category_table(MAIN / 'main_category.csv')
    t = {DISPLAY.get(k, k): v for k, v in t.items()}
    M = np.array([[t[m][c] for m in FIG7_METHODS] for c in CATS])
    cmap = LinearSegmentedColormap.from_list('seqblue', ['#ffffff'] + SEQUENTIAL_BLUE)
    fig, ax = plt.subplots(figsize=(4.9, 3.9))
    im = ax.imshow(M, cmap=cmap, vmin=0, vmax=100, aspect='auto')
    ax.grid(False)
    ax.set_xticks(range(len(FIG7_METHODS)), FIG7_METHODS, rotation=30, ha='right')
    ax.set_yticks(range(len(CATS)), [CAT[c] for c in CATS])
    ax.tick_params(length=0)
    for i in range(len(CATS)):
        for j in range(len(FIG7_METHODS)):
            ax.text(j, i, f'{M[i, j]:.1f}', ha='center', va='center', fontsize=6.3,
                    color='white' if M[i, j] > 58 else INK)
    group_bands(ax, len(FIG7_METHODS))
    ax.axvline(0.5, color=SURFACE, linewidth=2.5)
    for y, text in [((len(ONE_SESSION) - 1) / 2, 'evidence in\none session'),
                    (len(ONE_SESSION) + (len(FIVE_SESSIONS) - 1) / 2, 'evidence across\n5 sessions')]:
        ax.text(len(FIG7_METHODS) - 0.35, y, text, fontsize=6.5, color=INK_2, va='center', ha='left')
    cb = fig.colorbar(im, ax=ax, fraction=0.04, pad=0.22)
    cb.outline.set_visible(False)
    cb.set_label('LLM-judge accuracy (%)')
    rows = [{'category': c, 'method': m, 'judge_acc': t[m][c]} for c in CATS for m in FIG7_METHODS]
    save(fig, OUT / 'fig07_category_heatmap', rows, ['category', 'method', 'judge_acc'],
         [str(MAIN / 'main_category.csv')])


# --------------------------------------------------------------------------------------------- fig 9
ABL = [('w/o SUPERSEDE/EXPIRE', 'w/o close\n(SUPERSEDE/EXPIRE)'), ('w/o r_ind', 'w/o $r_{\\mathrm{ind}}$'),
       ('w/o r_evid', 'w/o $r_{\\mathrm{evid}}$')]


def fig09():
    style()
    t = category_table(MAIN / 'writer_ablation_category.csv')
    full = t['ShopMem (Writer + Reader)']
    overall = {r['method']: float(r['judge_acc']) for r in read_csv(MAIN / 'writer_ablation.csv')}
    pvals = {r['method']: r['mcnemar_p'] for r in read_csv(MAIN / 'writer_ablation.csv')}
    rows_lab = ['All questions'] + [CAT[c] for c in CATS]
    D = np.array([[overall[a] - overall['ShopMem (Writer + Reader)'] for a, _ in ABL]] +
                 [[t[a][c] - full[c] for a, _ in ABL] for c in CATS])
    cmap = LinearSegmentedColormap.from_list('div', [RED, '#ffffff', OURS])
    fig, ax = plt.subplots(figsize=(3.9, 3.9))
    im = ax.imshow(D, cmap=cmap, norm=TwoSlopeNorm(0, -12, 12), aspect='auto')
    ax.grid(False)
    ax.set_xticks(range(len(ABL)), [lab for _, lab in ABL])
    ax.xaxis.tick_top()
    ax.set_yticks(range(len(rows_lab)), rows_lab)
    ax.tick_params(length=0)
    for i in range(D.shape[0]):
        for j in range(D.shape[1]):
            v = D[i, j]
            ax.text(j, i, f'{v:+.1f}' if abs(v) >= 0.05 else '0', ha='center', va='center',
                    fontsize=6.3 if i else 6.6, fontweight='bold' if i == 0 else 'normal',
                    color='white' if abs(v) > 7 else (INK if abs(v) >= 2 else INK_3))
    ax.axhline(0.5, color=INK, linewidth=0.9)
    for y in (len(ONE_SESSION) + 0.5, len(ONE_SESSION) + len(FIVE_SESSIONS) + 0.5):
        ax.axhline(y, color=INK_3, linewidth=0.6)
    cb = fig.colorbar(im, ax=ax, fraction=0.05, pad=0.03)
    cb.outline.set_visible(False)
    cb.set_label('Change vs. full ShopMem (points)')
    rows = [{'row': 'all', 'ablation': a, 'delta': round(D[0, j], 2), 'mcnemar_p': pvals[a]} for j, (a, _) in enumerate(ABL)]
    rows += [{'row': c, 'ablation': a, 'delta': round(D[i + 1, j], 2), 'mcnemar_p': ''}
             for i, c in enumerate(CATS) for j, (a, _) in enumerate(ABL)]
    save(fig, OUT / 'fig09_writer_ablation_delta', rows, ['row', 'ablation', 'delta', 'mcnemar_p'],
         [str(MAIN / 'writer_ablation_category.csv'), str(MAIN / 'writer_ablation.csv')])


# --------------------------------------------------------------------------------------------- fig 10
STEPS = [('v2test90_K30_writer_sft_ep3_req', 'One-shot\nanswer'),
         ('v2test90_K30_tmax0_reader_sft_sol', '+ Reader\n($T_{\\max}=0$)'),
         ('v2test90_K30_reader_sft_sol', '+ supplem.\nretrieval'),
         ('v2test90_K30_grpo_stab1_final', '+ GRPO')]


def fig10():
    style()
    fig, axes = plt.subplots(1, 2, figsize=(DOUBLE, 2.6), gridspec_kw={'width_ratios': [1.15, 1]})
    ax = axes[0]
    acc = [round(100 * summary(r)['judge_acc_micro'], 1) for r, _ in STEPS]   # as printed in the tables
    rows = []
    prev = 0
    for i, ((run, lab), a) in enumerate(zip(STEPS, acc)):
        if i == 0:
            ax.bar(i, a, 0.62, color=INK_3, zorder=3)
            ax.text(i, a + 1.5, f'{a:.1f}', ha='center', fontsize=7, color=INK_2)
        else:
            ax.bar(i, prev, 0.62, color=OURS_LIGHT, alpha=0.35, zorder=2)
            ax.bar(i, a - prev, 0.62, bottom=prev, color=OURS, zorder=3)
            ax.text(i, a + 1.5, f'+{a - prev:.1f}', ha='center', fontsize=7, color=INK)
            ax.plot([i - 1 + 0.31, i - 0.31], [prev, prev], color=INK_3, linewidth=0.6, linestyle=':')
        rows.append({'panel': 'a', 'run': run, 'step': lab.replace('\n', ' '), 'judge_acc': round(a, 2)})
        prev = a
    ax.text(len(STEPS) - 1 + 0.36, acc[-1], f'{acc[-1]:.1f}', fontsize=7, color=INK, va='center')
    ax.set_xticks(range(len(STEPS)), [lab for _, lab in STEPS])
    ax.set_ylim(0, 100)
    ax.set_ylabel('LLM-judge accuracy on test90 (%)')
    ax.grid(axis='x', visible=False)
    panel_tag(ax, '(a)')
    ax = axes[1]
    g = read_csv('results/reader_grpo/grpo_curve.csv')
    for run, lab, c, m in [('reader_grpo_stab1', 'Stabilised GRPO (ours)', OURS, 'o'),
                           ('reader_grpo_lr1e5', 'Standard GRPO', NEUTRAL, 's')]:
        pts = sorted((int(r['step']), float(r['dev30_judge_acc'])) for r in g if r['run'] == run and r['dev30_judge_acc'])
        ax.plot([p[0] for p in pts], [p[1] for p in pts], color=c, marker=m, markersize=4, markeredgecolor=SURFACE,
                markeredgewidth=0.8, label=lab, zorder=3)
        rows += [{'panel': 'b', 'run': run, 'step': s, 'judge_acc': a} for s, a in pts]
    ax.set_xlabel('GRPO step')
    ax.set_ylabel('LLM-judge accuracy on dev30 (%)')
    ax.legend(loc='lower right')
    panel_tag(ax, '(b)')
    fig.tight_layout()
    save(fig, OUT / 'fig10_reader_ablation_grpo', rows, ['panel', 'run', 'step', 'judge_acc'],
         [str(RUNS / r) for r, _ in STEPS] + ['results/reader_grpo/grpo_curve.csv'])


# --------------------------------------------------------------------------------------------- fig 11
def retrieval_rates(run):
    d = defaultdict(lambda: [0, 0, 0])
    for p in jsonl(RUNS / run / 'predictions.jsonl'):
        s = p['meta'].get('searches', [])
        x = d[p['category']]
        x[0] += 1
        x[1] += bool(s)
        x[2] += any(q.get('include_closed') for q in s)
    return {c: (100 * v[1] / v[0], 100 * v[2] / v[0]) for c, v in d.items()}


def fig11():
    style()
    g = retrieval_rates('v2test90_K30_grpo_stab1_final')
    s = retrieval_rates('v2test90_K30_reader_sft_sol')
    fig, ax = plt.subplots(figsize=(DOUBLE, 2.5))
    x = np.arange(len(CATS))
    w = 0.38
    ax.bar(x - w / 2, [g[c][0] for c in CATS], w, color=OURS, edgecolor=SURFACE, linewidth=0.8, zorder=3,
           label='Supplementary retrieval')
    ax.bar(x + w / 2, [g[c][1] for c in CATS], w, color=SEQUENTIAL_BLUE[6], edgecolor=SURFACE, linewidth=0.8,
           zorder=3, label='… with closed versions ($\\eta=1$)')
    for i, c in enumerate(CATS):
        for dx, v in ((-w / 2, s[c][0]), (w / 2, s[c][1])):
            ax.plot([i + dx - w / 2 + 0.03, i + dx + w / 2 - 0.03], [v, v], color=INK, linewidth=1.0, zorder=4)
    ax.plot([], [], color=INK, linewidth=1.0, label='SFT Reader (before GRPO)')
    for c in ('working_memory_snapshot', 'state_tracking'):
        i = CATS.index(c)
        ax.text(i, g[c][0] + 3, f'{g[c][0]:.1f} / {g[c][1]:.1f}', ha='center', va='bottom', fontsize=6.5,
                color=INK, fontweight='bold')
    for xv in (len(ONE_SESSION) - 0.5, len(ONE_SESSION) + len(FIVE_SESSIONS) - 0.5):
        ax.axvline(xv, color=INK_3, linewidth=0.6)
    ax.set_xticks(x, [CAT[c] for c in CATS], rotation=32, ha='right')
    ax.set_ylim(0, 108)
    ax.set_ylabel('Share of questions (%)')
    ax.grid(axis='x', visible=False)
    ax.legend(ncol=3, loc='lower left', bbox_to_anchor=(0, 1.0))
    rows = [{'category': c, 'grpo_supplementary': round(g[c][0], 2), 'grpo_eta1': round(g[c][1], 2),
             'sft_supplementary': round(s[c][0], 2), 'sft_eta1': round(s[c][1], 2)} for c in CATS]
    save(fig, OUT / 'fig11_reader_retrieval_behaviour', rows, list(rows[0]),
         [str(RUNS / 'v2test90_K30_grpo_stab1_final'), str(RUNS / 'v2test90_K30_reader_sft_sol')])


# --------------------------------------------------------------------------------------------- fig 12
WRITERS = [('dev30_writer_8b_zs', 'Qwen3-8B\n(untrained)'), ('dev30_writer_luna', 'gpt-6-luna\n(teacher)'),
           ('dev30_writer_sft_ep3', 'ShopMem-Writer\n(4B)')]


def written_turns(trace):
    """T-ids cited by any extracted fact (the extractor's gate, before the manager; sample 0 only).
    This is the definition behind the dev30 gate table (precision 0.41 / 0.63 / 0.68)."""
    out = defaultdict(set)
    for w in jsonl(trace):
        if w.get('sample', 0) != 0:
            continue
        for f in w.get('facts', []):
            out[w['history_id']].update(f['turns'])
    return out


def density(pool_dir):
    """history_id -> list of turns (T-index order) with their density flag and type."""
    return {h['history_id']: flat_turns(h) for h in jsonl(pool_dir / 'K030/histories.jsonl')}


def gate_rates(written, turns_of, only=None):
    hi = lo = hi_w = lo_w = 0
    for hid, turns in turns_of.items():
        w = written.get(hid, set())
        for i, t in enumerate(turns, 1):
            if only and t['turn_type'] not in only:
                continue
            tid = f'T{i:05d}'
            if t['is_low_density']:
                lo += 1
                lo_w += tid in w
            else:
                hi += 1
                hi_w += tid in w
    return {'recall': 100 * hi_w / hi, 'low_written': 100 * lo_w / lo,
            'precision': 100 * hi_w / max(1, hi_w + lo_w)}


def fig12():
    style()
    dev = density(DEV30)
    rates = {run: gate_rates(written_turns(RUNS / run / 'trace.jsonl'), dev) for run, _ in WRITERS}
    fig = plt.figure(figsize=(DOUBLE, 4.0))
    gs = fig.add_gridspec(2, 2, height_ratios=[1.35, 1], width_ratios=[1.45, 1], hspace=0.9, wspace=0.35)
    ax = fig.add_subplot(gs[0, 0])
    x = np.arange(len(WRITERS))
    w = 0.36
    ax.bar(x - w / 2, [rates[r]['recall'] for r, _ in WRITERS], w, color=INK, label='High-density turns written',
           zorder=3)
    ax.bar(x + w / 2, [rates[r]['low_written'] for r, _ in WRITERS], w, color=INK_3, alpha=0.45,
           label='Low-density turns written', zorder=3)
    for i, (r, _) in enumerate(WRITERS):
        hi, lo = rates[r]['recall'], rates[r]['low_written']
        ax.annotate('', (i + w / 2, lo), (i + w / 2, hi), arrowprops=dict(arrowstyle='<->', color=OURS if i == 2 else INK_2,
                                                                      lw=0.8, shrinkA=0, shrinkB=0))
        ax.text(i + w / 2 + 0.06, (hi + lo) / 2, f'{hi - lo:.1f}', fontsize=6.5, color=OURS if i == 2 else INK_2,
                va='center')
    ax.set_xticks(x, [f'{lab}\nprecision {rates[r]["precision"] / 100:.2f}' for r, lab in WRITERS])
    ax.set_ylim(0, 118)
    ax.set_yticks(range(0, 101, 20))
    ax.set_ylabel('Turns cited by memory (%)')
    ax.grid(axis='x', visible=False)
    ax.legend(loc='upper center', ncol=2)
    panel_tag(ax, '(a)')
    # (b) tool returns on test90
    ax = fig.add_subplot(gs[0, 1])
    t90 = density(T90)
    banks = {}
    for f in (RUNS / 'v2test90_K30_writer_sft_ep3_req/banks').glob('*.jsonl'):
        banks[f.stem] = {tid for u in jsonl(f) for tid in u['source_turns']}
    tool = gate_rates(banks, t90, only={'tool_observation'})
    ax.bar([0, 1], [tool['recall'], tool['low_written']], 0.55, color=[INK, INK_3], alpha=1, zorder=3)
    ax.patches[1].set_alpha(0.45)
    for i, v in enumerate([tool['recall'], tool['low_written']]):
        ax.text(i, v + 2, f'{v:.1f}', ha='center', fontsize=7, color=INK)
    ax.set_xticks([0, 1], ['High-density\ntool returns', 'Routine\ntool returns'])
    ax.set_ylim(0, 118)
    ax.set_yticks(range(0, 101, 20))
    ax.set_ylabel('Written by ShopMem-Writer (%)')
    ax.grid(axis='x', visible=False)
    panel_tag(ax, '(b)')
    # (c) one dev30 scenario: annotation vs each writer
    ax = fig.add_subplot(gs[1, :])
    hid = sorted(dev)[0]
    turns = dev[hid]
    sc = turns[0]['dialog_id'].rsplit('_', 2)[0]
    idx = [i for i, t in enumerate(turns, 1) if t['dialog_id'].startswith(sc + '_')]
    lanes = [('Annotation', None)] + [(lab.replace('\n', ' '), r) for r, lab in WRITERS]
    wt = {r: written_turns(RUNS / r / 'trace.jsonl')[hid] for r, _ in WRITERS}
    sess_breaks = [k for k in range(1, len(idx)) if turns[idx[k] - 1]['dialog_id'].split('_')[3] !=
                   turns[idx[k - 1] - 1]['dialog_id'].split('_')[3]]
    for li, (lab, r) in enumerate(lanes):
        y = len(lanes) - 1 - li
        for k, i in enumerate(idx):
            t = turns[i - 1]
            if r is None:
                on, col = not t['is_low_density'], INK
            else:
                on, col = f'T{i:05d}' in wt[r], (OURS if r == WRITERS[-1][0] else INK_2)
            ax.add_patch(plt.Rectangle((k, y + 0.15), 0.85, 0.7, color=col if on else GRID, linewidth=0))
    for b in sess_breaks:
        ax.axvline(b - 0.08, color=INK_3, linewidth=0.6)
    ax.set_xlim(-0.5, len(idx) + 0.5)
    ax.set_ylim(0, len(lanes))
    ax.set_yticks([len(lanes) - 1 - i + 0.5 for i in range(len(lanes))],
                  ['High-density (annotation)'] + [f'Written: {lab}' for lab, _ in lanes[1:]])
    ax.set_xlabel(f'Turns of one dev30 scenario ({sc}), sessions separated by rules')
    ax.grid(False)
    ax.tick_params(axis='y', length=0)
    for sp in ('left', 'bottom'):
        ax.spines[sp].set_visible(False)
    ax.set_xticks([])
    panel_tag(ax, '(c)')
    rows = [{'panel': 'a', 'writer': r, **{k: round(v, 2) for k, v in rates[r].items()}} for r, _ in WRITERS]
    rows.append({'panel': 'b', 'writer': 'v2test90_K30_writer_sft_ep3_req', **{k: round(v, 2) for k, v in tool.items()}})
    save(fig, OUT / 'fig12_write_gate', rows, ['panel', 'writer', 'recall', 'low_written', 'precision'],
         [str(RUNS / r) for r, _ in WRITERS] + [str(RUNS / 'v2test90_K30_writer_sft_ep3_req')])


# --------------------------------------------------------------------------------------------- fig 13
def render_turn_line(t):
    """memeval.methods.base.render_turn on a raw turn dict (what full context and the token stats count)."""
    kind = t['turn_type'] if not t.get('tool_name') else f"{t['turn_type']}:{t['tool_name']}"
    return f"[{t['timestamp']}] {t['speaker_id']} ({t['speaker_role']}, {kind}): {t['text']}"


def render_unit(u, turn_by_id):
    """ShopMem._render(e) on a bank row (header + content + up to 3 verbatim tool returns); reproduces the
    harness memory 'tokens' exactly for the ShopMem-Prompt banks."""
    import re
    from datetime import datetime
    from memeval.methods.shopmem import EVIDENCE_LABEL, SHIFT_LABEL, SOURCE_LABEL, SOURCE_NAMES, TOOL_TYPES
    names = SOURCE_NAMES['en']
    head = [datetime.fromisoformat(u.get('time') or u['record_time']).strftime('%Y-%m-%d %H:%M')]
    if u.get('shift'):
        head.append(f"{SHIFT_LABEL['en']}: {u['shift']}")
    src = u['source'].split(',') if isinstance(u['source'], str) else u['source']
    head.append(f"{SOURCE_LABEL['en']}: " + '+'.join(f'{names.get(k, k)}({d})' if d else names.get(k, k)
                                                     for k, _, d in (x.partition(':') for x in src)))
    norm = lambda x: re.sub(r'\s+', '', x)  # noqa: E731
    ev = [turn_by_id[t]['text'] for t in u['source_turns'] if t in turn_by_id
          and turn_by_id[t]['turn_type'] in TOOL_TYPES and norm(turn_by_id[t]['text']) not in norm(u['content'])]
    return f"[{' | '.join(head)}] {u['content']}" + ''.join(f"\n    {EVIDENCE_LABEL['en']}: {x}" for x in ev[:3])


def fig13():
    """Growth over one test90 history. Token definitions follow the harness: raw = formatted turns + session
    headers (what full context reads); memory = rendered units of the default retrieval view, i.e. ShopMem's
    valid non-request units (final value = summary 'tokens') and all ShopMem-Prompt units (no closing)."""
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
    ours = [u for u in jsonl(RUNS / 'v2test90_K30_writer_sft_ep3_req/banks' / f'{hid}.jsonl')
            if u['source'] != 'request']
    prompt = jsonl(RUNS / 'mfail2_banks_shopmem_v1/banks' / f'{hid}.jsonl')
    ours_tok, prompt_tok = np.zeros(n + 1), np.zeros(n + 1)
    active, closed = np.zeros(n + 1), np.zeros(n + 1)
    for u in ours:
        k = count_tokens(render_unit(u, tb))
        ours_tok[wend[u['window']]] += k
        active[wend[u['window']]] += 1
        if u.get('closed_window') is not None:
            ours_tok[wend[u['closed_window']]] -= k
            active[wend[u['closed_window']]] -= 1
            closed[wend[u['closed_window']]] += 1
    for u in prompt:
        prompt_tok[min(n, max(int(t[1:]) for t in u['source_turns']))] += count_tokens(render_unit(u, tb))
    ours_tok, prompt_tok = np.cumsum(ours_tok)[1:], np.cumsum(prompt_tok)[1:]
    active, closed = np.cumsum(active)[1:], np.cumsum(closed)[1:]
    xs = np.arange(1, n + 1)
    fig, axes = plt.subplots(1, 2, figsize=(DOUBLE, 2.5))
    ax = axes[0]
    for y, c, lab in ((raw, INK_3, 'Raw interaction'), (prompt_tok, OURS_LIGHT, 'ShopMem-Prompt memory'),
                      (ours_tok, OURS, 'ShopMem memory (valid units)')):
        ax.plot(xs, y / 1e3, color=c, label=lab)
        ax.annotate(f'{y[-1] / 1e3:.0f}k', (n, y[-1] / 1e3), xytext=(4, 0), textcoords='offset points',
                    fontsize=6.5, color=INK_2, va='center')
    ax.set_xlabel('Turns processed')
    ax.set_ylabel('Cumulative tokens (thousands)')
    ax.legend(loc='upper left')
    panel_tag(ax, '(a)')
    ax = axes[1]
    ax.stackplot(xs, active, closed, colors=[OURS, SEQUENTIAL_BLUE[1]], labels=['Valid units', 'Closed units'],
                 linewidth=0)
    ax.annotate(f'{int(active[-1])} valid / {int(closed[-1])} closed', (n, active[-1] + closed[-1]),
                xytext=(-4, 6), textcoords='offset points', ha='right', fontsize=6.5, color=INK_2)
    ax.set_xlabel('Turns processed')
    ax.set_ylabel('Memory units')
    ax.legend(loc='upper left')
    panel_tag(ax, '(b)')
    fig.tight_layout()
    rows = [{'turn': int(x), 'raw_tokens': int(raw[x - 1]), 'prompt_memory_tokens': int(prompt_tok[x - 1]),
             'shopmem_memory_tokens': int(ours_tok[x - 1]), 'valid_units': int(active[x - 1]),
             'closed_units': int(closed[x - 1])} for x in list(range(25, n, 25)) + [n]]
    save(fig, OUT / 'fig13_memory_growth', rows, list(rows[0]),
         [str(RUNS / 'v2test90_K30_writer_sft_ep3_req/banks'), str(RUNS / 'mfail2_banks_shopmem_v1/banks')])


# --------------------------------------------------------------------------------------------- fig 14
K_SERIES = [('grpo_stab1_final', 'ShopMem', OURS, 'o'), ('rag_hybrid_rr', 'RAG hybrid+rerank', NEUTRAL, 's'),
            ('full_context_plain', 'Full context', INK_2, '^')]


def fig14():
    style()
    Ks = [1, 10, 30, 90]
    fig, axes = plt.subplots(1, 3, figsize=(DOUBLE, 2.3))
    rows = []
    panels = [('judge_acc_micro', 'LLM-judge accuracy (%)', 100), ('turn_recall', 'Evidence recall (%)', 100),
              ('context_tokens_mean', 'Context tokens per question', 1)]
    for k, (ax, (key, ylabel, scale)) in enumerate(zip(axes, panels)):
        for tag, lab, c, m in K_SERIES:
            ys = [summary(f'v2test90_K{K}_{tag}')[key] * scale for K in Ks]
            ax.plot(Ks, ys, color=c, marker=m, markersize=4, markeredgecolor=SURFACE, markeredgewidth=0.8,
                    label=lab, zorder=3)
            if tag == 'full_context_plain':
                ax.plot(Ks[2:], ys[2:], linestyle='none', marker=m, markersize=4, markerfacecolor=SURFACE,
                        markeredgecolor=c, markeredgewidth=1.0, zorder=4)
            if k == 0:
                rows += [{'series': lab, 'K': K, **{p[0]: None for p in panels}} for K in Ks]
            for K, y in zip(Ks, ys):
                next(r for r in rows if r['series'] == lab and r['K'] == K)[key] = round(y, 2)
        ax.set_xscale('log')
        ax.set_xticks(Ks, [str(K) for K in Ks])
        ax.minorticks_off()
        ax.set_xlabel('Scenarios per history K (log)')
        ax.set_ylabel(ylabel)
        if key == 'context_tokens_mean':
            ax.set_yscale('log')
        else:
            ax.set_ylim(0, 100)
        panel_tag(ax, f'({"abc"[k]})')
    h, lab = axes[0].get_legend_handles_labels()
    h.append(plt.Line2D([], [], linestyle='none', marker='^', markerfacecolor=SURFACE, markeredgecolor=INK_3))
    lab.append('Context truncated to 100k')
    fig.legend(h, lab, ncol=4, loc='upper center', bbox_to_anchor=(0.5, 1.06))
    fig.tight_layout()
    save(fig, OUT / 'fig14_history_length', rows, ['series', 'K'] + [p[0] for p in panels],
         [str(RUNS / f'v2test90_K{K}_{t}') for K in Ks for t, *_ in K_SERIES])


FIGS = {'fig05': fig05, 'fig06': fig06, 'fig07': fig07, 'fig09': fig09, 'fig10': fig10, 'fig11': fig11,
        'fig12': fig12, 'fig13': fig13, 'fig14': fig14}

if __name__ == '__main__':
    names = sys.argv[1:] or ['all']
    for n in (list(FIGS) if names == ['all'] else names):
        FIGS[n]()
