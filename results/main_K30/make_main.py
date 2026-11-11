"""E2 main experiment table (K=30, eval pool, 3510 questions, dataset v0.3).

    .venv/bin/python results/main_K30/make_main.py        # from the M-locomo root

Writes, next to this script:
- main_table.csv / main_table.md: per-category LLM-judge accuracy, macro accuracy, mean context
  tokens per question, write-time cost (LLM calls, tokens, ingest time), query-time LLM calls,
  and ShopMem's paired-bootstrap difference against every baseline;
- main_table.meta.json: command, run directories, bootstrap settings.
The figures are drawn by memeval.figures (commands in make_figures.sh).

Paired bootstrap: questions are resampled within each category (every category keeps its size,
so the macro average stays balanced), 10,000 resamples, seed 0; p is the one-sided share of
resamples in which ShopMem's macro accuracy does not exceed the baseline's.
"""
import csv
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from memeval.figures import CATEGORY_LABELS  # noqa: E402

OUT = Path(__file__).resolve().parent
RUNS = [  # (run dir, display name); ShopMem first
    ('main_K30_shopmem', 'ShopMem (ours)'),
    ('main_K30_rag_hybrid_rr', 'RAG-hybrid + rerank'),
    ('main_K30_rag_hybrid', 'RAG-hybrid'),
    ('main_K30_rag_dense', 'RAG-dense'),
    ('main_K30_rag_bm25', 'RAG-BM25'),
    ('main_K30_full_context_plain', 'Full context'),
    ('main_K30_full_context', 'Full context (reasoning)'),
    ('main_K30_observation_mask', 'Obs. mask'),
    ('main_K30_sliding_window', 'Sliding window (= ShopMem tokens)'),
    ('main_K30_simplemem', 'SimpleMem'),
    ('main_K30_oracle_gate_rag', 'Oracle gate + RAG (reads gold)'),
]
N_BOOT, SEED = 10_000, 0


def read_jsonl(p):
    return [json.loads(x) for x in Path(p).read_text().splitlines() if x.strip()]


def main():
    runs_dir = ROOT / 'runs'
    summ = {r: json.loads((runs_dir / r / 'summary.json').read_text()) for r, _ in RUNS}
    scores = {r: {x['question_id']: bool(x['correct']) for x in read_jsonl(runs_dir / r / 'scores.jsonl')}
              for r, _ in RUNS}
    for r, _ in RUNS:
        s = summ[r]
        assert s['questions'] == 3510 and s['K'] == 30 and s['pool'] == 'eval', r
        assert all(v is not None for v in read_jsonl(runs_dir / r / 'scores.jsonl') for v in [v['correct']]), r
    ref = read_jsonl(runs_dir / RUNS[0][0] / 'scores.jsonl')
    cat = {x['question_id']: x['category'] for x in ref}
    cats = [c for c in CATEGORY_LABELS if c in set(cat.values())]
    by_cat = defaultdict(list)
    for q, c in cat.items():
        by_cat[c].append(q)
    for r, _ in RUNS:
        assert set(scores[r]) == set(cat), f'{r}: different question set'

    def macro(r, sample):
        return 100 * sum(sum(scores[r][q] for q in qs) / len(qs) for qs in sample.values()) / len(sample)

    full = {c: by_cat[c] for c in cats}
    rng = random.Random(SEED)
    boots = [{c: [rng.choice(qs) for _ in qs] for c, qs in full.items()} for _ in range(N_BOOT)]
    ours = RUNS[0][0]
    ours_boot = [macro(ours, b) for b in boots]

    rows = []
    for r, name in RUNS:
        s = summ[r]
        row = {'method': name, 'run': r}
        for c in cats:
            row[c] = round(100 * s['by_category'][c]['judge_acc'], 1)
        row['macro_acc'] = round(macro(r, full), 1)
        row['ctx_tokens'] = round(s['context_tokens_mean'])
        row['event_recall'] = round(100 * s['event_recall'], 1) if s['event_recall'] is not None else ''
        row['write_llm_calls'] = int(s['write_llm_calls'])
        row['write_tokens_M'] = round(s['write_tokens'] / 1e6, 2)
        row['ingest_s'] = round(s['ingest_s_total'], 1)
        row['query_llm_calls_per_q'] = round(s['retrieve_llm_calls_per_q'], 2)
        row['retrieve_s_per_q'] = round(s['retrieve_s_mean'], 2)
        if r != ours:
            diffs = [a - macro(r, b) for a, b in zip(ours_boot, boots)]
            diffs_sorted = sorted(diffs)
            row['ours_minus'] = round(macro(ours, full) - macro(r, full), 1)
            row['ci95'] = f'[{diffs_sorted[int(0.025 * N_BOOT)]:.1f}, {diffs_sorted[int(0.975 * N_BOOT)]:.1f}]'
            row['p_one_sided'] = round(sum(d <= 0 for d in diffs) / N_BOOT, 4)
        rows.append(row)

    fields = ['method', 'run', *cats, 'macro_acc', 'ctx_tokens', 'event_recall', 'write_llm_calls', 'write_tokens_M',
              'ingest_s', 'query_llm_calls_per_q', 'retrieve_s_per_q', 'ours_minus', 'ci95', 'p_one_sided']
    with open(OUT / 'main_table.csv', 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    head = ['Method', *[CATEGORY_LABELS[c] for c in cats], 'Macro', 'Tokens/q', 'Write calls', 'Write tok (M)',
            'Query LLM calls/q', 'Δ ours [95% CI]', 'p']
    md = ['| ' + ' | '.join(head) + ' |', '|' + '---|' * len(head)]
    for row in rows:
        best = {c: max(x[c] for x in rows if 'Oracle' not in x['method']) for c in cats + ['macro_acc']}
        cell = lambda c: f"**{row[c]}**" if row[c] == best[c] and 'Oracle' not in row['method'] else str(row[c])
        diff = f"{row['ours_minus']:+.1f} {row['ci95']}" if 'ours_minus' in row else '–'
        md.append('| ' + ' | '.join([row['method'], *[cell(c) for c in cats], cell('macro_acc'),
                                     f"{row['ctx_tokens']:,}", str(row['write_llm_calls']), str(row['write_tokens_M']),
                                     str(row['query_llm_calls_per_q']), diff,
                                     str(row.get('p_one_sided', '–'))]) + ' |')
    note = ('\nK=30, eval pool (90 scenarios, 3510 questions, 270 per category), dataset v0.3. Answers by Qwen3-8B '
            '(thinking off), judged by gpt-6-luna. Every retrieval method uses Qwen3-Embedding-8B; ShopMem and '
            'RAG-hybrid + rerank use Qwen3-Reranker-4B. All runs use the reasoning answer format except "Full context", '
            'which uses the plain format (its better one). Bold: best non-oracle value. Δ: ShopMem minus the row, '
            f'paired bootstrap within categories ({N_BOOT:,} resamples); p one-sided.\n')
    (OUT / 'main_table.md').write_text('\n'.join(md) + '\n' + note)
    (OUT / 'main_table.meta.json').write_text(json.dumps(
        {'command': 'python results/main_K30/make_main.py', 'runs': [f'runs/{r}' for r, _ in RUNS],
         'bootstrap': {'resamples': N_BOOT, 'seed': SEED, 'stratified_by': 'category', 'test': 'one-sided'}},
        indent=2))
    print('\n'.join(md))


if __name__ == '__main__':
    main()
