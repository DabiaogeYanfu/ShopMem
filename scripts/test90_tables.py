"""test90 main-table and ablation numbers (luna judge, 3510 q, EN K=30) from runs/v2test90_K30_* ->
results/test90_main/{main.csv, category.csv, ablation.csv, meta.json}. McNemar is exact, two-sided, vs the SFT Writer."""
import csv, json, sys
from math import comb
from pathlib import Path
MAIN = [('ShopMem-Writer (SFT)', 'writer_sft_ep3'), ('ShopMem v1', 'shopmem_v1'), ('RAG hybrid+rerank', 'rag_hybrid_rr'),
        ('RAG hybrid', 'rag_hybrid'), ('RAG dense', 'rag_dense'), ('RAG BM25', 'rag_bm25'), ('SimpleMem', 'simplemem'),
        ('Full context (plain)', 'full_context_plain'), ('Observation mask', 'observation_mask'),
        ('Sliding window', 'sliding_window')]
ABL = [('SFT', 'writer_sft_ep3'), ('w/o SUPERSEDE/EXPIRE', 'writer_abl_add_noop'), ('w/o r_ind', 'writer_abl_no_rind'),
       ('w/o r_evid', 'writer_abl_no_revid')]
out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
S = lambda r: {x['question_id']: x for x in map(json.loads, open(f'runs/v2test90_K30_{r}/scores.jsonl'))}
ref = S('writer_sft_ep3')
def mcnemar(A):
    b = sum(ref[q]['correct'] and not A[q]['correct'] for q in ref); c = sum(A[q]['correct'] and not ref[q]['correct'] for q in ref)
    return b, c, min(1, 2 * sum(comb(b + c, i) for i in range(min(b, c) + 1)) / 2 ** (b + c)) if b + c else 1.0
def table(spec, path):
    rows, cats = [], []
    for name, r in spec:
        s, A = json.load(open(f'runs/v2test90_K30_{r}/summary.json')), S(r)
        b, c, p = mcnemar(A)
        rows.append({'method': name, 'run': f'v2test90_K30_{r}', 'judge_acc': round(100 * s['judge_acc_macro'], 1),
                     'judge_acc_answerable': round(100 * s['judge_acc_answerable'], 1), 'f1_answerable': round(100 * s['f1_answerable'], 1), 'context_tokens': round(s['context_tokens_mean']),
                     'memory_entries': s.get('memory_entries'), 'sft_only': b, 'method_only': c, 'mcnemar_p': f'{p:.2g}'})
        for k in sorted({x['category'] for x in A.values()}):
            qs = [q for q, x in A.items() if x['category'] == k]
            cats.append({'method': name, 'category': k, 'judge_acc': round(100 * sum(A[q]['correct'] for q in qs) / len(qs), 1)})
    for f, data in ((path, rows), (path.replace('.csv', '_category.csv'), cats)):
        with open(out / f, 'w', newline='') as fh:
            w = csv.DictWriter(fh, fieldnames=list(data[0])); w.writeheader(); w.writerows(data)
    return rows
for r in table(MAIN, 'main.csv') + table(ABL, 'ablation.csv'):
    print(r['method'], r['judge_acc'], r['context_tokens'], r['mcnemar_p'])
(out / 'meta.json').write_text(json.dumps({
    'pool': 'test90 (EN, K=30, 3 histories, 3510 questions)', 'answerer': 'Qwen3-8B (no thinking), reasoning format',
    'judge': 'gpt-6-luna, per item', 'notes': ['full_context_plain uses max_context_tokens=100000 (tiktoken) ~= 123-127k Qwen3 '
    'tokens; the default 120k overflows the 8B 131072 window', 'sliding_window budget = 1019 = SFT Writer mean ctx',
    'SimpleMem: official defaults (db80b6a)', 'ablations retrain SFT on ablated data (no_rind/no_revid) or change the '
    'write actions (add_noop); their SFT data is 92% / 81% identical to the main SFT set'],
    'script': 'scripts/test90_tables.py'}, indent=1))
