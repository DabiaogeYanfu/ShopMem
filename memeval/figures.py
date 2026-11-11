"""Paper figures from run directories, with their data and provenance saved alongside.

Every figure is written as <out>.pdf (vector, fonts embedded, for the manuscript),
<out>.png (300 dpi preview), <out>.csv (the plotted numbers) and <out>.meta.json (the
command line and source runs), so any figure can be regenerated or audited.

    python -m memeval.figures pareto   --runs runs/a runs/b ... --out results/pilot/pareto
    python -m memeval.figures category --runs runs/a runs/b ... --metric judge_acc --out results/pilot/by_category
    python -m memeval.figures curve    --runs runs/k1 runs/k10 ... --x K --series method --out results/scaling/acc_vs_K
        [--logx] [--hollow runs/k90_full_context --hollow-label 'Truncated to 100k tokens']
    python -m memeval.figures heatmap  --csv depths.csv --out results/retrieval/depth_heatmap
    python -m memeval.figures sft      --run runs/sft_writer --out results/sft_writer/sft_curve
    python -m memeval.figures grpo     --runs runs/reader_grpo_stab1 runs/reader_grpo_lr1e5 \
        --sft-run runs/dev30_gate1_reader_sft_sol --out results/reader_grpo/grpo_curve

--label RUN=TEXT names one run (e.g. two full_context runs that differ only in answer format);
the run keeps its method's colour.

Styling (shared by all figures): single-column width 3.5 in (double 7.16 in), 8 pt
sans-serif text, light recessive grid, one y-axis. Colour follows the method, never
its rank: every known method has a fixed colour (FIXED_COLOR); the proposed method is
always slot 1 (blue) and SimpleMem slot 2 (orange). In scatter plots only those two are
coloured and the other baselines are neutral grey told apart by marker shape and direct
labels (a scatter can only carry three all-pairs-distinguishable hues). Bars carry a
2 px surface gap between neighbours.
"""
import argparse
import csv
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

SURFACE = '#fcfcfb'
INK, INK_2, INK_3 = '#0b0b0b', '#52514e', '#8a8984'
GRID = '#e4e3df'
NEUTRAL = '#8a8984'
# validated categorical order (adjacent CVD dE >= 9.1, normal-vision >= 19.6 on the light surface)
CATEGORICAL = ['#2a78d6', '#eb6834', '#1baf7a', '#eda100', '#e87ba4', '#008300']
SEQUENTIAL_BLUE = ['#cde2fb', '#9ec5f4', '#6da7ec', '#3987e5', '#256abf', '#184f95', '#0d366b']
PROPOSED = {'shopmem', 'shopmem_reader', 'ours'}   # method names drawn as "ours" (slot 1)
# fixed per-method colours so a method looks the same in every figure; unknown methods fall back to grey
FIXED_COLOR = {'simplemem': CATEGORICAL[1], 'rag_hybrid': CATEGORICAL[2], 'full_context': CATEGORICAL[3],
               'rag_dense': CATEGORICAL[4], 'rag_bm25': CATEGORICAL[5], 'mem0': '#4a3aa7', 'lightmem': '#e34948'}
MARKERS = ['o', 's', '^', 'D', 'v', 'P', 'X', '<', '>']
SINGLE, DOUBLE = 3.5, 7.16

LABELS = {'full_context': 'Full context', 'sliding_window': 'Sliding window', 'observation_mask': 'Obs. mask',
          'oracle_gate': 'Oracle gate', 'rag_dense': 'RAG-dense', 'rag_bm25': 'RAG-BM25', 'rag_hybrid': 'RAG-hybrid',
          'oracle_gate_rag': 'Oracle gate + RAG', 'simplemem': 'SimpleMem', 'mem0': 'Mem0', 'lightmem': 'LightMem',
          'shopmem': 'ShopMem (ours)'}
# canonical category order for every figure: single-fact -> multi-event -> industrial-specific -> unanswerable
CATEGORY_LABELS = {'single_hop_fact': 'Single-hop', 'state_tracking': 'State', 'constraint_recall': 'Constraint',
                   'failed_intervention': 'Failed interv.', 'superseded_fact': 'Superseded',
                   'conditioned_parameter': 'Measures', 'temporal_order': 'Temporal order',
                   'multi_hop_event': 'Multi-hop', 'cross_role_dependency': 'Cross-role',
                   'working_memory_snapshot': 'Working mem.', 'tool_evidence_recall': 'Tool evidence',
                   'shift_temporal': 'Shift', 'unanswerable': 'Unanswerable'}


def style():
    plt.rcParams.update({
        'font.family': 'sans-serif', 'font.sans-serif': ['Arial', 'Helvetica', 'DejaVu Sans'],
        'font.size': 8, 'axes.titlesize': 8, 'axes.labelsize': 8, 'xtick.labelsize': 7, 'ytick.labelsize': 7,
        'legend.fontsize': 7, 'legend.frameon': False,
        'axes.edgecolor': INK_3, 'axes.labelcolor': INK_2, 'xtick.color': INK_2, 'ytick.color': INK_2,
        'text.color': INK, 'axes.linewidth': 0.6, 'xtick.major.width': 0.6, 'ytick.major.width': 0.6,
        'axes.grid': True, 'grid.color': GRID, 'grid.linewidth': 0.5, 'axes.axisbelow': True,
        'axes.spines.top': False, 'axes.spines.right': False,
        'figure.facecolor': SURFACE, 'axes.facecolor': SURFACE, 'savefig.facecolor': SURFACE,
        'lines.linewidth': 1.5, 'lines.markersize': 5,
        'pdf.fonttype': 42, 'ps.fonttype': 42,  # embed TrueType, as journals require
    })


RUN_LABELS = {}  # run dir name -> display label, from --label


def load_summary(run_dir):
    s = json.loads((Path(run_dir) / 'summary.json').read_text())
    s['run_dir'] = str(run_dir)
    return s


def label_of(s):
    return RUN_LABELS.get(Path(s['run_dir']).name, LABELS.get(s['method'], s['method']))


def method_color(name, order):
    if name in PROPOSED:
        return CATEGORICAL[0]
    if name in FIXED_COLOR:
        return FIXED_COLOR[name]
    return order


def save(fig, out, rows, fieldnames, runs):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out.with_suffix('.pdf'), bbox_inches='tight')
    fig.savefig(out.with_suffix('.png'), dpi=300, bbox_inches='tight')
    with open(out.with_suffix('.csv'), 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    out.with_suffix('.meta.json').write_text(json.dumps(
        {'command': 'python -m memeval.figures ' + ' '.join(sys.argv[1:]), 'runs': runs}, indent=2))
    plt.close(fig)
    print(f'wrote {out}.pdf/.png/.csv/.meta.json')


# candidate label offsets in points, tried in order: beside the marker first, then further out
# (with a leader line) when the near slots collide with another label or marker
_NEAR = [(6, 2), (6, -9), (-6, 2), (-6, -9), (0, 7), (0, -13)]
_FAR = [(dx, dy) for dy in (0, 10, -10, 20, -20, 30, -30, 40, -40) for dx in (40, -40, 70, -70)]


def place_labels(fig, ax, points, fontsize=7):
    """Direct labels that never overlap each other, a marker, or the axes frame.

    points: (x, y, text, colour) in data coordinates. Labels are placed greedily from the top
    point down; each takes the first candidate offset whose rendered box is free, and a label
    pushed to a far slot gets a thin leader line back to its marker.
    """
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    to_px = ax.transData.transform
    pt = fig.dpi / 72
    frame = ax.get_window_extent(renderer)
    markers = [to_px((x, y)) for x, y, _, _ in points]
    boxes = []

    def free(bb):
        pad = 1.5 * pt
        if bb.x0 < frame.x0 or bb.x1 > frame.x1 + 60 * pt or bb.y0 < frame.y0 or bb.y1 > frame.y1:
            return False
        if any(bb.x0 - pad < o.x1 and o.x0 < bb.x1 + pad and bb.y0 - pad < o.y1 and o.y0 < bb.y1 + pad for o in boxes):
            return False
        r = 4 * pt  # marker radius
        return not any(bb.x0 - r < mx < bb.x1 + r and bb.y0 - r < my < bb.y1 + r for mx, my in markers)

    for (x, y, text, color) in sorted(points, key=lambda p: -p[1]):
        for i, (dx, dy) in enumerate(_NEAR + _FAR):
            far = i >= len(_NEAR)
            ha = 'left' if dx > 0 else 'right' if dx < 0 else 'center'
            ann = ax.annotate(text, (x, y), xytext=(dx, dy), textcoords='offset points', fontsize=fontsize,
                              color=color, ha=ha, va='bottom' if dy >= 0 else 'top',
                              arrowprops=dict(arrowstyle='-', color=GRID if far else 'none', lw=0.6,
                                              shrinkA=0, shrinkB=3) if far else None)
            bb = ann.get_window_extent(renderer)
            if free(bb) or i == len(_NEAR) + len(_FAR) - 1:
                boxes.append(bb)
                break
            ann.remove()


def pareto(runs, out, metric='judge_acc_macro', title=None):
    """Accuracy vs mean context tokens per question (log x); highlights ours and SimpleMem."""
    style()
    S = [load_summary(r) for r in runs]
    fig, ax = plt.subplots(figsize=(SINGLE, 2.6))
    rows = []
    for i, s in enumerate(S):
        m, x, y = s['method'], s['context_tokens_mean'], 100 * (s[metric] or 0)
        highlighted = m in PROPOSED or m == 'simplemem'
        color = method_color(m, NEUTRAL) if highlighted else NEUTRAL
        ax.scatter(x, y, s=42 if highlighted else 26, marker='o' if highlighted else MARKERS[1 + i % 8],
                   color=color, edgecolor=SURFACE, linewidth=1.2, zorder=3)
        rows.append({'method': m, 'label': label_of(s), 'run': s['run'], 'context_tokens_mean': round(x, 1),
                     metric: round(y, 2)})
    ax.set_xscale('log')
    ax.set_xlabel('Context tokens per question (log scale)')
    ax.set_ylabel('LLM-judge accuracy (%)' if 'judge' in metric else metric)
    ax.set_ylim(0, max(r[metric] for r in rows) * 1.2 + 2)
    place_labels(fig, ax, [(r['context_tokens_mean'], r[metric], r['label'],
                            INK if r['method'] in PROPOSED or r['method'] == 'simplemem' else INK_2) for r in rows])
    if title:
        ax.set_title(title, loc='left', color=INK)
    save(fig, out, rows, ['method', 'label', 'run', 'context_tokens_mean', metric], runs)


def category(runs, out, metric='judge_acc', title=None):
    """Grouped bars: one group per question category, one bar per method."""
    style()
    S = [load_summary(r) for r in runs]
    present = set(S[0]['by_category'])
    cats = [c for c in CATEGORY_LABELS if c in present] + sorted(present - set(CATEGORY_LABELS))
    n = len(S)
    width = 0.8 / n
    fig, ax = plt.subplots(figsize=(DOUBLE, 2.4))
    x = np.arange(len(cats))
    rows = []
    for i, s in enumerate(S):
        m = s['method']
        color = method_color(m, NEUTRAL)
        vals = [100 * (s['by_category'][c][metric] or 0) for c in cats]
        # 2 px surface gap between adjacent bars comes from the white edge
        ax.bar(x - 0.4 + width * (i + 0.5), vals, width, color=color, edgecolor=SURFACE, linewidth=1.0,
               label=label_of(s), zorder=3)
        rows += [{'method': m, 'label': label_of(s), 'category': c, metric: round(v, 2)} for c, v in zip(cats, vals)]
    ax.set_xticks(x, [CATEGORY_LABELS.get(c, c) for c in cats], rotation=35, ha='right')
    ax.set_ylabel({'judge_acc': 'LLM-judge accuracy (%)', 'f1': 'F1 (%)',
                   'event_recall': 'Event-level evidence recall (%)'}.get(metric, metric))
    ax.set_ylim(0, 105)
    ax.legend(ncol=min(n, 6), loc='upper left', bbox_to_anchor=(0, 1.16))
    ax.grid(axis='x', visible=False)
    if title:
        ax.set_title(title, loc='left', color=INK, pad=18)
    save(fig, out, rows, ['method', 'label', 'category', metric], runs)


def curve(runs, out, x_key, series_key='method', metric='judge_acc_macro', xlabel=None, logx=False, title=None,
          hollow=(), hollow_label=None):
    """Metric vs a numeric setting (K, top_k, ...) with one line per series.

    Series are named by --label on any of their runs (else LABELS). Points of the runs in `hollow`
    are drawn as open markers, explained by one legend entry (`hollow_label`, e.g. truncated context).
    Each line ends in its last value; with few distinct x values the ticks sit on exactly those values."""
    style()
    S = [load_summary(r) for r in runs]
    hollow = {Path(r).name for r in hollow}

    def xval(s):
        if x_key in s:
            return s[x_key]
        cfg = json.loads((Path(s['run_dir']) / 'config.json').read_text())
        return cfg.get('method_config', {}).get(x_key, cfg.get(x_key))

    series, names = {}, {}
    for s in S:
        run = Path(s['run_dir']).name
        series.setdefault(s[series_key], []).append((float(xval(s)), 100 * (s[metric] or 0), run))
        if run in RUN_LABELS:
            names[s[series_key]] = RUN_LABELS[run]
    fig, ax = plt.subplots(figsize=(SINGLE, 2.4))
    rows = []
    for i, (name, pts) in enumerate(series.items()):
        pts.sort()
        color, marker = method_color(name, NEUTRAL), MARKERS[i % 9]
        ax.plot([p[0] for p in pts], [p[1] for p in pts], color=color, linewidth=1.4, zorder=3,
                label=names.get(name, LABELS.get(name, name)), marker=marker, markersize=4.5,
                markeredgecolor=SURFACE, markeredgewidth=0.8)
        for x, y, run in pts:
            if run in hollow:  # open marker over the filled one
                ax.plot([x], [y], linestyle='none', marker=marker, markersize=4.5, markerfacecolor=SURFACE,
                        markeredgecolor=color, markeredgewidth=1.0, zorder=4)
        x, y, _ = pts[-1]
        ax.annotate(f'{y:.1f}', (x, y), xytext=(5, 0), textcoords='offset points', fontsize=7, color=INK_2,
                    ha='left', va='center')
        rows += [{'series': name, x_key: p[0], metric: round(p[1], 2), 'run': p[2], 'hollow': p[2] in hollow}
                 for p in pts]
    if logx:
        ax.set_xscale('log')
    xs = sorted({r[x_key] for r in rows})
    if len(xs) <= 8:
        ax.set_xticks(xs)
        ax.set_xticklabels([f'{v:g}' for v in xs])
        ax.minorticks_off()
        lo, hi = (xs[0] / 1.35, xs[-1] * 1.6) if logx else (xs[0], xs[-1])
        ax.set_xlim(lo, hi)
    ax.set_xlabel(xlabel or x_key)
    if 'judge' in metric:
        ax.set_ylim(0, 100)
    ax.set_ylabel('LLM-judge accuracy (%)' if 'judge' in metric else metric)
    handles, labels = ax.get_legend_handles_labels()
    if hollow and hollow_label:
        handles.append(plt.Line2D([], [], linestyle='none', marker='o', markersize=4.5, markerfacecolor=SURFACE,
                                  markeredgecolor=INK_3, markeredgewidth=1.0))
        labels.append(hollow_label)
    ax.legend(handles, labels, loc='lower left')
    if title:
        ax.set_title(title, loc='left', color=INK)
    save(fig, out, rows, ['series', x_key, metric, 'run', 'hollow'], runs)


def heatmap(csv_path, out, row_key, col_key, value_key, title=None, fmt='{:.1f}'):
    """Sequential (single-hue blue) heatmap from a long-format CSV, values printed in cells."""
    style()
    data = list(csv.DictReader(open(csv_path)))
    rows_ = list(dict.fromkeys(d[row_key] for d in data))
    cols = list(dict.fromkeys(d[col_key] for d in data))
    M = np.full((len(rows_), len(cols)), np.nan)
    for d in data:
        M[rows_.index(d[row_key]), cols.index(d[col_key])] = float(d[value_key])
    cmap = matplotlib.colors.LinearSegmentedColormap.from_list('seqblue', SEQUENTIAL_BLUE)
    fig, ax = plt.subplots(figsize=(SINGLE, 0.25 * len(rows_) + 0.8))
    im = ax.imshow(M, cmap=cmap, aspect='auto')
    ax.grid(False)
    ax.set_xticks(range(len(cols)), cols)
    ax.set_yticks(range(len(rows_)), [CATEGORY_LABELS.get(r, r) for r in rows_])
    lo, hi = np.nanmin(M), np.nanmax(M)
    for i in range(len(rows_)):
        for j in range(len(cols)):
            if not np.isnan(M[i, j]):
                dark = (M[i, j] - lo) / ((hi - lo) or 1) > 0.55
                ax.text(j, i, fmt.format(M[i, j]), ha='center', va='center', fontsize=6.5,
                        color='white' if dark else INK)
    fig.colorbar(im, ax=ax, fraction=0.05, pad=0.03).outline.set_visible(False)
    if title:
        ax.set_title(title, loc='left', color=INK)
    save(fig, out, data, list(data[0].keys()), [str(csv_path)])


def sft(run, out, title=None):
    """Writer SFT curve from scripts/train_writer_sft.py's log.jsonl: per-step training loss (grey)
    and the per-role validation loss (extractor slot 1, manager slot 2); dotted lines mark epoch ends."""
    style()
    log = [json.loads(line) for line in open(Path(run) / 'log.jsonl') if line.strip()]
    train = [e for e in log if 'loss' in e]
    val = [e for e in log if 'val' in e]
    fig, ax = plt.subplots(figsize=(SINGLE, 2.4))
    ax.plot([e['step'] for e in train], [e['loss'] for e in train], color=NEUTRAL, linewidth=0.8, alpha=0.7,
            label='Train', zorder=2)
    for i, role in enumerate(('extractor', 'manager')):
        pts = [(e['step'], e['val'][role]) for e in val if role in e['val']]
        ax.plot([p[0] for p in pts], [p[1] for p in pts], color=CATEGORICAL[i], marker=MARKERS[i], markersize=4,
                markeredgecolor=SURFACE, markeredgewidth=0.8, label=f'Val ({role})', zorder=3)
    ends = sorted({e['step'] for e in train if e['step'] == max(t['step'] for t in train if t['epoch'] == e['epoch'])})
    for s in ends[:-1]:
        ax.axvline(s, color=INK_3, linewidth=0.6, linestyle=':', zorder=1)
    ax.set_xlabel('Optimizer step')
    ax.set_ylabel('Reply-token cross-entropy')
    ax.set_ylim(bottom=0)
    ax.legend()
    if title:
        ax.set_title(title, loc='left', color=INK)
    rows = [{'step': e['step'], 'epoch': e.get('epoch', ''), 'train_loss': e.get('loss', ''),
             'val_extractor': e.get('val', {}).get('extractor', ''), 'val_manager': e.get('val', {}).get('manager', '')}
            for e in log if 'step' in e]
    save(fig, out, rows, ['step', 'epoch', 'train_loss', 'val_extractor', 'val_manager'], [str(run)])


def topr(run, out, title=None):
    """Writer TOPR curve from scripts/train_writer_topr.py's log.jsonl: top, mean per-token log pi of the
    positive and negative val samples (slots 1, 2); bottom, per-step mean truncated ratio [pi/mu]_0^1 of the
    negatives (grey)."""
    style()
    log = [json.loads(line) for line in open(Path(run) / 'log.jsonl') if line.strip()]
    train = [e for e in log if 'loss' in e]
    val = [e for e in log if 'val' in e]
    fig, (ax, bx) = plt.subplots(2, 1, figsize=(SINGLE, 3.6), sharex=True, gridspec_kw={'height_ratios': [3, 2]})
    for i, (k, name) in enumerate((('pos', 'positives'), ('neg', 'negatives'))):
        pts = [(e['step'], e['val'][k]) for e in val if k in e['val']]
        ax.plot([p[0] for p in pts], [p[1] for p in pts], color=CATEGORICAL[i], marker=MARKERS[i], markersize=4,
                markeredgecolor=SURFACE, markeredgewidth=0.8, label=f'Val ({name})', zorder=3)
    ax.set_ylabel('Per-token log $\\pi$')
    ax.legend()
    bx.plot([e['step'] for e in train], [e['neg_ratio_mean'] for e in train], color=NEUTRAL, linewidth=0.8,
            alpha=0.7, zorder=2)
    bx.set_xlabel('Optimizer step')
    bx.set_ylabel('Negatives $[\\pi/\\mu]_0^1$')
    bx.set_ylim(0, 1.05)
    if title:
        ax.set_title(title, loc='left', color=INK)
    rows = [{'step': e['step'], 'train_loss': e.get('loss', ''), 'neg_ratio_mean': e.get('neg_ratio_mean', ''),
             'val_pos_logp': e.get('val', {}).get('pos', ''), 'val_neg_logp': e.get('val', {}).get('neg', '')}
            for e in log if 'step' in e]
    save(fig, out, rows, ['step', 'train_loss', 'neg_ratio_mean', 'val_pos_logp', 'val_neg_logp'], [str(run)])


GRPO_PANELS = [('dev', 'Dev30 judge acc. (%)'), ('acc', 'Rollout acc.'), ('searched', 'Searched share'),
               ('reply_tokens_mean', 'Reply tokens'), ('entropy', 'Entropy'), ('kl_per_token', 'KL to ref.')]


def grpo(runs, out, sft_run=None, title=None):
    """Reader GRPO curves, one colour per run (slot 1 = first run): dev30 judge accuracy of the saved
    checkpoints (runs/dev30_<tag>_step<k>; runs/dev30_<tag>_final at the last step of a finished run; the SFT
    Reader --sft-run at step 0 and as a dashed reference), and per-step training signals from log.jsonl."""
    style()
    fig, axes = plt.subplots(3, 2, figsize=(DOUBLE, 5.4), sharex=True)
    rows, sources = [], []
    sft_acc = load_summary(sft_run)['judge_acc_macro'] * 100 if sft_run else None
    xmax = 0
    for i, run in enumerate(runs):
        run = Path(run)
        tag = run.name.replace('reader_', '', 1)
        log = [json.loads(line) for line in open(run / 'log.jsonl') if line.strip()]
        steps = [e for e in log if 'entropy' in e]
        last = steps[-1]['step']
        xmax = max(xmax, last)
        dev = [(0, sft_acc)] if sft_acc is not None else []
        for d in sorted(run.parent.glob(f'dev30_{tag}_step*'), key=lambda d: int(d.name.rsplit('step', 1)[1])):
            if (d / 'summary.json').exists():
                dev.append((int(d.name.rsplit('step', 1)[1]), load_summary(d)['judge_acc_macro'] * 100))
        final = run.parent / f'dev30_{tag}_final'
        if (final / 'summary.json').exists() and any(e.get('done') for e in log) and last not in dict(dev):
            dev.append((last, load_summary(final)['judge_acc_macro'] * 100))
        color = CATEGORICAL[i]
        for ax, (key, ylabel) in zip(axes.flat, GRPO_PANELS):
            if key == 'dev':
                ax.plot([x for x, _ in dev], [y for _, y in dev], color=color, marker=MARKERS[i], markersize=4,
                        markeredgecolor=SURFACE, markeredgewidth=0.8, label=RUN_LABELS.get(run.name, run.name),
                        zorder=3)
            else:
                ax.plot([e['step'] for e in steps], [e[key] for e in steps], color=color, linewidth=1.0, zorder=3)
            ax.set_ylabel(ylabel)
        rows += [{'run': run.name, 'step': x, 'dev30_judge_acc': round(y, 2)} for x, y in dev]
        rows += [{'run': run.name, 'step': e['step'], **{k: e[k] for k, _ in GRPO_PANELS[1:]}} for e in steps]
        sources.append(str(run))
    dev_ax = axes.flat[0]
    if sft_acc is not None:
        dev_ax.axhline(sft_acc, color=NEUTRAL, linewidth=0.8, linestyle='--', zorder=2)
        dev_ax.annotate('SFT Reader', (xmax, sft_acc), xytext=(0, 3), textcoords='offset points',
                        ha='right', fontsize=7, color=INK_2)
        sources.append(str(sft_run))
    for ax in axes[-1]:
        ax.set_xlabel('GRPO step')
    fig.legend(*dev_ax.get_legend_handles_labels(), loc='upper center', ncol=len(runs), bbox_to_anchor=(0.5, 1.02))
    if title:
        fig.suptitle(title, x=0.01, ha='left', color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    save(fig, out, rows, ['run', 'step', 'dev30_judge_acc'] + [k for k, _ in GRPO_PANELS[1:]], sources)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='kind', required=True)
    for k in ('pareto', 'category', 'curve'):
        q = sub.add_parser(k)
        q.add_argument('--runs', nargs='+', required=True)
        q.add_argument('--label', action='append', default=[], metavar='RUN=TEXT')
        q.add_argument('--out', required=True)
        q.add_argument('--metric')
        q.add_argument('--title')
        if k == 'curve':
            q.add_argument('--x', required=True)
            q.add_argument('--series', default='method')
            q.add_argument('--xlabel')
            q.add_argument('--logx', action='store_true')
            q.add_argument('--hollow', nargs='+', default=[], help='runs drawn with open markers')
            q.add_argument('--hollow-label', help='legend entry explaining the open markers')
    h = sub.add_parser('heatmap')
    for a in ('--csv', '--out', '--row', '--col', '--value'):
        h.add_argument(a, required=True)
    h.add_argument('--title')
    t = sub.add_parser('sft')
    t.add_argument('--run', required=True, help='a scripts/train_writer_sft.py output dir (log.jsonl)')
    t.add_argument('--out', required=True)
    t.add_argument('--title')
    r = sub.add_parser('topr')
    r.add_argument('--run', required=True, help='a scripts/train_writer_topr.py output dir (log.jsonl)')
    r.add_argument('--out', required=True)
    r.add_argument('--title')
    g = sub.add_parser('grpo')
    g.add_argument('--runs', nargs='+', required=True, help='scripts/train_reader_grpo.py output dirs')
    g.add_argument('--sft-run', help='dev30 run of the SFT Reader (drawn at step 0)')
    g.add_argument('--label', action='append', default=[], metavar='RUN=TEXT')
    g.add_argument('--out', required=True)
    g.add_argument('--title')
    a = p.parse_args()
    for item in getattr(a, 'label', []):
        run, text = item.split('=', 1)
        RUN_LABELS[Path(run).name] = text
    if a.kind == 'pareto':
        pareto(a.runs, a.out, a.metric or 'judge_acc_macro', a.title)
    elif a.kind == 'category':
        category(a.runs, a.out, a.metric or 'judge_acc', a.title)
    elif a.kind == 'curve':
        curve(a.runs, a.out, a.x, a.series, a.metric or 'judge_acc_macro', a.xlabel, a.logx, a.title,
              a.hollow, a.hollow_label)
    elif a.kind == 'sft':
        sft(a.run, a.out, a.title)
    elif a.kind == 'grpo':
        grpo(a.runs, a.out, a.sft_run, a.title)
    elif a.kind == 'topr':
        topr(a.run, a.out, a.title)
    else:
        heatmap(a.csv, a.out, a.row, a.col, a.value, a.title)


if __name__ == '__main__':
    main()
