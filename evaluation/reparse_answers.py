"""Re-extract `prediction` from `raw_answer` with the current memeval.prompts.parse_answer.

    python evaluation/reparse_answers.py runs/<run> [runs/<run> ...]

For reasoning-format runs whose answers were parsed before parse_answer learned to recover the
"answer" field from JSON that does not parse. Changed rows lose their judge label in
scores.jsonl (set to null), so the next `memeval.score` judges exactly those rows again.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from memeval.prompts import parse_answer  # noqa: E402


def reparse(run_dir):
    run_dir = Path(run_dir)
    pred_path, score_path = run_dir / 'predictions.jsonl', run_dir / 'scores.jsonl'
    rows = [json.loads(x) for x in pred_path.read_text().splitlines() if x.strip()]
    changed = set()
    for r in rows:
        if 'raw_answer' in r:
            new = parse_answer(r['raw_answer'])
            if new != r['prediction']:
                r['prediction'] = new
                changed.add(r['question_id'])
    if not changed:
        return 0
    pred_path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
    if score_path.exists():
        scores = [json.loads(x) for x in score_path.read_text().splitlines() if x.strip()]
        for s in scores:
            if s['question_id'] in changed:
                s['correct'] = s['judge_raw'] = None
        score_path.write_text(''.join(json.dumps(s, ensure_ascii=False) + '\n' for s in scores))
    return len(changed)


if __name__ == '__main__':
    for d in sys.argv[1:]:
        print(d, reparse(d), 'answers re-extracted')
