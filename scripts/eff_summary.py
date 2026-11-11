"""RQ3 efficiency table from runs/eff_* (uncached, one test90 history) -> results/test90_efficiency/."""
import csv, json, re, statistics, sys
from pathlib import Path
out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
rows = []
for name, run in (('ShopMem-Writer (SFT, 4B)', 'eff_writer_sft_ep3'), ('ShopMem v1 (8B prompt)', 'eff_shopmem_v1')):
    h = json.loads(open(f'runs/{run}/histories.jsonl').readline())
    u = json.load(open(f'runs/{run}/usage.json'))
    P = [json.loads(l) for l in open(f'runs/{run}/predictions.jsonl')]
    w = u['write']; m = h['memory']
    rows.append({
        'method': name, 'turns': h['turns'], 'windows': m['windows'],
        'ingest_wall_s': round(h['ingest_s'], 1), 'ingest_ms_per_turn': round(1000 * h['ingest_s'] / h['turns'], 1),
        'write_calls': int(w['calls']), 'write_prompt_tokens': int(w['prompt_tokens']),
        'write_completion_tokens': int(w['completion_tokens']),
        'write_tokens_per_window': round((w['prompt_tokens'] + w['completion_tokens']) / m['windows']),
        'memory_entries': m['entries'], 'memory_tokens': m['tokens'],
        'retrieve_s_median': round(statistics.median(p['retrieve_s'] for p in P), 2),
        'retrieve_s_p90': round(sorted(p['retrieve_s'] for p in P)[int(0.9 * len(P))], 2),
        'context_tokens_mean': round(statistics.mean(p['context_tokens'] for p in P)),
        'questions': len(P)})
with open(out / 'efficiency.csv', 'w', newline='') as f:
    wr = csv.DictWriter(f, fieldnames=list(rows[0])); wr.writeheader(); wr.writerows(rows)
(out / 'meta.json').write_text(json.dumps({'source_runs': ['runs/eff_writer_sft_ep3', 'runs/eff_shopmem_v1'],
    'setting': 'test90 H_EN_TEST90_K030_001 (1725 turns), fresh empty LLM/embedding/rerank caches, write_workers 16, '
               'Writer: Qwen3-4B+LoRA vLLM on one RTX 3090 (GPU 7); v1 writes with Qwen3-8B tp=2 (GPUs 3,4); '
               'answerer Qwen3-8B; 130 questions (10/category)', 'script': 'scripts/eff_summary.py'}, indent=1))
for r in rows:
    print(json.dumps(r))
