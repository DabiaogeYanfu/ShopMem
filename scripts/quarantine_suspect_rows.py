"""Second quarantine (2026-10-10): suspect pre-fix micu rows (FormatRetry resampled a glued reply: 210;
replayed yan-gpt cache replies: 45) and every sol verdict written by the pre-fix Responses client.
Moved, not deleted; generation / judge loops re-create them with the fixed client."""
import json, sqlite3, shutil, time
from pathlib import Path
import scripts.cache_quarantine_micu_prefix as cq
from memeval.data import read_jsonl, load
from memeval.prompts import judge_messages
qdb = sqlite3.connect('.cache/quarantine_micu_prefix.sqlite')
_, qs, _ = load('en', 'train', 20); Q = {x.question_id: x for x in qs}
stamp = time.strftime('%Y%m%d_%H%M%S')

def write(path, rows):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
    tmp.replace(path)

def move(path, out_path, pred):
    rs = read_jsonl(path)
    keep, out = [r for r in rs if not pred(r)], [r for r in rs if pred(r)]
    with open(out_path, 'a') as fh:
        fh.writelines(json.dumps(r, ensure_ascii=False) + '\n' for r in out)
    write(path, keep)
    print(f'{path}: kept {len(keep)}, moved {len(out)} -> {out_path.name}')

for run in ['reader_cold_sol_K20_micu', 'reader_cold_sol_K20']:
    d = Path('runs') / run
    shutil.copytree(d, Path('runs/_backup') / f'{run}.{stamp}')
    sus = set()
    if (d / 'suspect_keys.txt').exists() and run.endswith('micu'):
        sus = {(l.split('\t')[0], int(l.split('\t')[1])) for l in (d / 'suspect_keys.txt').read_text().splitlines()}
        k = lambda r: (r['question_id'], r['sample']) in sus
        move(d / 'trajectories.jsonl', d / 'quarantine_suspect.jsonl', k)
        move(d / 'judge_gpt-6-luna.jsonl', d / 'judge_gpt-6-luna.quarantine.jsonl', k)
    T = {(r['question_id'], r['sample']): r for r in read_jsonl(d / 'trajectories.jsonl')}

    def pre_fix_verdict(j):
        key = (j['question_id'], j['sample'])
        if key in sus or key not in T:
            return True
        x = Q[j['question_id']]
        msgs = judge_messages(x.category, x.question, x.reference, T[key]['prediction'])
        return qdb.execute('SELECT 1 FROM c WHERE k=?', (cq.key(cq.SOL, msgs, cq.JUDGE),)).fetchone() is not None
    move(d / 'judge_gpt-6.1-sol.jsonl', d / 'judge_gpt-6.1-sol.quarantine.jsonl', pre_fix_verdict)
