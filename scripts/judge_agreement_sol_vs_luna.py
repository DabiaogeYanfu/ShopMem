"""luna vs sol judge agreement on the sol Reader cold-start trajectories (same trajectory, both judges).
-> results/judge_agreement_sol_vs_luna/{summary.json, by_category.csv, disagreements_sample.jsonl,
   judge_agreement.{pdf,png}}      python -m scripts.judge_agreement_sol_vs_luna"""
import collections, csv, json, random, re
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from memeval.data import read_jsonl

OUT = Path('results/judge_agreement_sol_vs_luna'); OUT.mkdir(parents=True, exist_ok=True)
RUNS = {'reader_cold_sol_K20': 'yan', 'reader_cold_sol_K20_micu': 'micu'}
pairs = []
for run, src in RUNS.items():
    T = {(r['question_id'], r['sample']): r for r in read_jsonl(f'runs/{run}/trajectories.jsonl')}
    L = {(v['question_id'], v['sample']): v for v in read_jsonl(f'runs/{run}/judge_gpt-6-luna.jsonl')} if src == 'micu' else None
    for s in read_jsonl(f'runs/{run}/judge_gpt-6.1-sol.jsonl'):
        k = (s['question_id'], s['sample'])
        if k not in T:
            continue
        t = T[k]
        luna_c, luna_raw = (L[k]['correct'], L[k]['judge_raw']) if L else (t['correct'], t['judge_raw'])
        pairs.append({'source': src, 'question_id': k[0], 'sample': k[1], 'category': t['category'],
                      'luna': luna_c, 'sol': s['correct'], 'luna_raw': luna_raw, 'sol_raw': s['judge_raw'],
                      'pred_words': len(t['prediction'].split()), 'prediction': t['prediction']})


def kappa(ps):
    n = len(ps); a = sum(p['luna'] == p['sol'] for p in ps) / n
    pl = sum(p['luna'] for p in ps) / n; pss = sum(p['sol'] for p in ps) / n
    pe = pl * pss + (1 - pl) * (1 - pss)
    return a, (a - pe) / (1 - pe) if pe < 1 else 1.0


def summ(ps):
    a, k = kappa(ps)
    c = collections.Counter((p['luna'], p['sol']) for p in ps)
    return {'n': len(ps), 'luna_acc': round(sum(p['luna'] for p in ps) / len(ps), 4),
            'sol_acc': round(sum(p['sol'] for p in ps) / len(ps), 4), 'agreement': round(a, 4), 'kappa': round(k, 4),
            'luna_T_sol_F': c[(True, False)], 'luna_F_sol_T': c[(False, True)],
            'both_T': c[(True, True)], 'both_F': c[(False, False)]}


REASONS = [('adds unsupported details', r'\b(adds?|added|adding|additional|extra|unsupported|not (?:in|supported by|established by|stated in) the gold|beyond the gold)\b'),
           ('contradicts gold', r'\b(contradict|conflict|incorrect|wrong|inconsistent)\b'),
           ('omits required element', r'\b(omit|omits|missing|fails to|does not (?:mention|state|identify|include)|lacks)\b')]


def reason(raw):
    try:
        t = json.loads(raw[raw.index('{'): raw.rindex('}') + 1])['reason'].lower()
    except Exception:
        return 'unparsed'
    for name, pat in REASONS:
        if re.search(pat, t):
            return name
    return 'other'


summary = {'all': summ(pairs), 'by_source': {s: summ([p for p in pairs if p['source'] == s]) for s in RUNS.values()}}
dis = [p for p in pairs if p['luna'] and not p['sol']]
summary['sol_wrong_luna_right_reasons'] = dict(collections.Counter(reason(p['sol_raw']) for p in dis).most_common())
rev = [p for p in pairs if not p['luna'] and p['sol']]
summary['luna_wrong_sol_right_reasons'] = dict(collections.Counter(reason(p['luna_raw']) for p in rev).most_common())
both = [p for p in pairs if p['luna'] == p['sol']]
summary['pred_words_median'] = {'agree': sorted(p['pred_words'] for p in both)[len(both) // 2],
                                'luna_T_sol_F': sorted(p['pred_words'] for p in dis)[len(dis) // 2] if dis else None}
cats = sorted({p['category'] for p in pairs})
by_cat = {c: summ([p for p in pairs if p['category'] == c]) for c in cats}
summary['by_category'] = by_cat
(OUT / 'summary.json').write_text(json.dumps(summary, indent=2))
with open(OUT / 'by_category.csv', 'w', newline='') as fh:
    w = csv.writer(fh); w.writerow(['category', 'n', 'luna_acc', 'sol_acc', 'agreement', 'kappa', 'luna_T_sol_F', 'luna_F_sol_T'])
    for c, v in by_cat.items():
        w.writerow([c, v['n'], v['luna_acc'], v['sol_acc'], v['agreement'], v['kappa'], v['luna_T_sol_F'], v['luna_F_sol_T']])
random.seed(0)
with open(OUT / 'disagreements_sample.jsonl', 'w') as fh:
    for p in random.sample(dis, min(40, len(dis))) + random.sample(rev, min(20, len(rev))):
        fh.write(json.dumps({k: p[k] for k in ['source', 'question_id', 'sample', 'category', 'luna', 'sol', 'prediction', 'luna_raw', 'sol_raw']}, ensure_ascii=False) + '\n')

# figure: per-category accuracy under each judge (sorted by gap), kappa annotated
order = sorted(cats, key=lambda c: by_cat[c]['luna_acc'] - by_cat[c]['sol_acc'])
LUNA_C, SOL_C, INK, INK2, GRID = '#2a78d6', '#eb6834', '#0b0b0b', '#52514e', '#e4e3df'
plt.rcParams.update({'font.size': 9, 'font.family': 'DejaVu Sans', 'axes.edgecolor': INK2, 'axes.labelcolor': INK2,
                     'xtick.color': INK2, 'ytick.color': INK, 'svg.fonttype': 'none', 'pdf.fonttype': 42})
fig, ax = plt.subplots(figsize=(7.0, 5.6))
y = range(len(order)); h = 0.36
ax.barh([i + h / 2 + 0.01 for i in y], [by_cat[c]['luna_acc'] * 100 for c in order], h, color=LUNA_C, label='luna judge')
ax.barh([i - h / 2 - 0.01 for i in y], [by_cat[c]['sol_acc'] * 100 for c in order], h, color=SOL_C, label='sol judge')
for i, c in enumerate(order):
    ax.text(101, i, f"κ {by_cat[c]['kappa']:.2f}  (n={by_cat[c]['n']})", va='center', fontsize=7.5, color=INK2)
ax.set_yticks(list(y)); ax.set_yticklabels([c.replace('_', ' ') for c in order])
ax.set_xlim(0, 100); ax.set_xlabel('trajectories judged correct (%)')
ax.xaxis.grid(True, color=GRID, lw=0.6); ax.set_axisbelow(True)
for s in ('top', 'right'): ax.spines[s].set_visible(False)
a = summary['all']
fig.suptitle(f"Same sol-teacher trajectories judged by luna and by sol (n={a['n']})", x=0.02, ha='left', fontsize=10, color=INK)
fig.text(0.02, 0.925, f"luna {a['luna_acc']*100:.1f}% vs sol {a['sol_acc']*100:.1f}% judged correct  ·  agreement "
         f"{a['agreement']*100:.1f}%, Cohen's κ {a['kappa']:.2f}  ·  sorted by the luna − sol gap",
         ha='left', fontsize=8, color=INK2)
ax.legend(loc='upper center', bbox_to_anchor=(0.5, -0.09), ncol=2, frameon=False, fontsize=8)
fig.subplots_adjust(left=0.27, right=0.80, top=0.89, bottom=0.14)
fig.savefig(OUT / 'judge_agreement.pdf'); fig.savefig(OUT / 'judge_agreement.png', dpi=200)
print(json.dumps({k: v for k, v in summary.items() if k != 'by_category'}, indent=1))
for c in order[::-1]:
    v = by_cat[c]; print(f"  {c:24s} n={v['n']:4d} luna {v['luna_acc']:.2f} sol {v['sol_acc']:.2f} κ {v['kappa']:.2f} T/F {v['luna_T_sol_F']:3d} F/T {v['luna_F_sol_T']:3d}")
