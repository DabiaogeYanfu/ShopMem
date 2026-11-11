"""v3 test90 tables (luna judge, 3510 q, EN K=30) from runs/v2test90_K30_* -> results/test90_main_v3/
{main, reader_ablation, writer_ablation}.csv (+ *_category.csv) and meta.json. Full method = SFT Writer
(request units) + GRPO Reader (stab1, final ckpt). McNemar is exact, two-sided, vs the table's reference row.
v2 tables (one-shot answerer) stay in results/test90_main via scripts/test90_tables.py."""
import csv, json, sys
from math import comb
from pathlib import Path
FULL = 'grpo_stab1_final'
MAIN = [('ShopMem (Writer + Reader)', FULL), ('ShopMem-Writer (SFT), one-shot', 'writer_sft_ep3_req'),
        ('ShopMem v1', 'shopmem_v1'), ('RAG hybrid+rerank', 'rag_hybrid_rr'), ('RAG hybrid', 'rag_hybrid'),
        ('RAG dense', 'rag_dense'), ('RAG BM25', 'rag_bm25'), ('SimpleMem', 'simplemem'),
        ('Full context (plain)', 'full_context_plain'), ('Observation mask', 'observation_mask'),
        ('Sliding window', 'sliding_window')]
READER = [('ShopMem (Writer + Reader)', FULL), ('w/o stabilised GRPO (standard GRPO, best ckpt)', 'grpo_lr1e5_step20'),
          ('w/o GRPO (SFT Reader)', 'reader_sft_sol'), ('w/o search (SFT Reader, T_max=0)', 'tmax0_reader_sft_sol'),
          ('w/o Reader (one-shot answerer)', 'writer_sft_ep3_req')]
WRITER = [('ShopMem (Writer + Reader)', FULL), ('w/o SUPERSEDE/EXPIRE', f'writer_abl_add_noop_{FULL}'),
          ('w/o r_ind', f'writer_abl_no_rind_{FULL}'), ('w/o r_evid', f'writer_abl_no_revid_{FULL}'),
          ('One-shot answerer: SFT Writer', 'writer_sft_ep3_req'), ('One-shot: w/o SUPERSEDE/EXPIRE', 'writer_abl_add_noop_req'),
          ('One-shot: w/o r_ind', 'writer_abl_no_rind_req'), ('One-shot: w/o r_evid', 'writer_abl_no_revid_req')]
out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
S = lambda r: {x['question_id']: x for x in map(json.loads, open(f'runs/v2test90_K30_{r}/scores.jsonl'))}


def mcnemar(ref, A):
    b = sum(ref[q]['correct'] and not A[q]['correct'] for q in ref); c = sum(A[q]['correct'] and not ref[q]['correct'] for q in ref)
    return b, c, min(1, 2 * sum(comb(b + c, i) for i in range(min(b, c) + 1)) / 2 ** (b + c)) if b + c else 1.0


def table(spec, path, refs):
    rows, cats = [], []
    for name, r in spec:
        s, A = json.load(open(f'runs/v2test90_K30_{r}/summary.json')), S(r)
        assert len(A) == 3510 and all(x['correct'] is not None for x in A.values()), r
        ref = refs(r)
        b, c, p = mcnemar(S(ref), A)
        rows.append({'method': name, 'run': f'v2test90_K30_{r}', 'judge_acc': round(100 * s['judge_acc_macro'], 1),
                     'judge_acc_answerable': round(100 * s['judge_acc_answerable'], 1), 'f1_answerable': round(100 * s['f1_answerable'], 1),
                     'context_tokens': round(s['context_tokens_mean']), 'memory_entries': s.get('memory_entries'),
                     'reference': f'v2test90_K30_{ref}', 'ref_only': b, 'method_only': c,
                     'mcnemar_p': f'{p:.2g}' if p > 0 or ref == r else '<1e-300'})
        for k in sorted({x['category'] for x in A.values()}):
            qs = [q for q, x in A.items() if x['category'] == k]
            cats.append({'method': name, 'category': k, 'judge_acc': round(100 * sum(A[q]['correct'] for q in qs) / len(qs), 1)})
    for f, data in ((path, rows), (path.replace('.csv', '_category.csv'), cats)):
        with open(out / f, 'w', newline='') as fh:
            w = csv.DictWriter(fh, fieldnames=list(data[0])); w.writeheader(); w.writerows(data)
    return rows


# Writer ablations are compared within the same answerer: trained Reader rows vs FULL, one-shot rows vs the one-shot SFT Writer
writer_ref = lambda r: 'writer_sft_ep3_req' if r.endswith('_req') else FULL
for f, spec, refs in (('main.csv', MAIN, lambda r: FULL), ('reader_ablation.csv', READER, lambda r: FULL),
                      ('writer_ablation.csv', WRITER, writer_ref)):
    print('==', f)
    for r in table(spec, f, refs):
        print(f"  {r['method']:50s} {r['judge_acc']:5.1f}  {r['ref_only']}/{r['method_only']}  p={r['mcnemar_p']}")
(out / 'meta.json').write_text(json.dumps({
    'pool': 'test90 (EN, K=30, 3 histories, 3510 questions)', 'judge': 'gpt-6-luna, per item',
    'full_method': 'SFT Writer ep3 (request units) banks runs/v2test90_K30_writer_sft_ep3_req/banks + Qwen3-8B Reader '
                   '(sol-SFT cold start + stabilised GRPO runs/reader_grpo_stab1, final adapter = end of 1 epoch, 68 steps), T_max=2',
    'stabilised_grpo': 'lr 1e-5, beta 0.02, advantage without std normalisation (Dr. GRPO), sample 32 -> train 8 per group '
                       'with --select-weight, TIS cap 2, 1 epoch over the GRPO pool (mixed-outcome train questions, 1355)',
    'standard_grpo': 'runs/reader_grpo_lr1e5 (lr 1e-5, beta 0.001, std-normalised advantage, sample 16 -> train 8, no select-weight, no TIS); diverged after ~step 20, best dev30 ckpt step20',
    'notes': ['baselines (v1, RAG, SimpleMem, full context, observation mask, sliding window) are the v2 runs, unchanged',
              'one-shot answerer = Qwen3-8B (no thinking), reasoning format, as in v2',
              'Writer ablations retrain SFT on ablated data (no_rind/no_revid) or change the write actions (add_noop); '
              'all Writer banks include request units'],
    'script': 'scripts/test90_tables_v3.py'}, indent=1))
