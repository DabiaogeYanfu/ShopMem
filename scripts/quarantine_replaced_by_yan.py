"""Replace the 255 suspect pre-fix micu pairs by yan-gpt re-generations (user decision 2026-10-10): their
micu re-generations (fixed client) and verdicts move to *.replaced_by_yan.jsonl; nothing is deleted.
Run only when no reader_cold process of the micu run is alive (the chain script checks)."""
import json, shutil, time
from pathlib import Path
from memeval.data import read_jsonl
d = Path('runs/reader_cold_sol_K20_micu')
keys = {(l.split()[0], int(l.split()[1])) for l in (d / 'redo_on_yan_keys.txt').read_text().splitlines() if l.strip()}
assert len(keys) == 255
shutil.copytree(d, Path('runs/_backup') / f'{d.name}.{time.strftime("%Y%m%d_%H%M%S")}')
for name in ['trajectories', 'judge_gpt-6-luna', 'judge_gpt-6.1-sol']:
    p = d / f'{name}.jsonl'
    rs = read_jsonl(p)
    keep = [r for r in rs if (r['question_id'], r['sample']) not in keys]
    out = [r for r in rs if (r['question_id'], r['sample']) in keys]
    with open(d / f'{name}.replaced_by_yan.jsonl', 'a') as fh:
        fh.writelines(json.dumps(r, ensure_ascii=False) + '\n' for r in out)
    tmp = p.with_suffix('.tmp')
    tmp.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in keep))
    tmp.replace(p)
    print(f'{p.name}: kept {len(keep)}, moved {len(out)}')
