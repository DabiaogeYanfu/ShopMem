"""LLM audit of gold labels and scenario consistency (layer 4 of the data audit).

(a) question audit: for every question, a strong LLM sees the full dialogue of the
    question's own scenario (with dialog ids, timestamps, roles), the site's shift
    calendar, as_of, the question, the gold reference and the cited evidence turns, and
    returns a verdict on whether the gold is correct, complete, unambiguous and grounded.
(b) scenario audit: per scenario, the LLM lists internal contradictions (numbers,
    states, times, roles) and industrially implausible content.

Scenario-level context is enough: display names are unique corpus-wide (audit_splits.py),
so a question cannot be answered differently by a distractor scenario of its K=30 history.

Resumable: rows already in the output files are skipped; LLM calls are disk-cached.
Usage:
  python evaluation/audit_gold_llm.py --lang zh --splits test test_ext \
      --model gpt-6-luna --base-url https://api.openai.com/v1 --api-key-env JUDGE_API_KEY --workers 8
"""
import argparse
import json
import re
import sys
import threading
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from memeval.data import DATA_DIRS, SPLITS, _reference_text, read_jsonl  # noqa: E402
from memeval.llm import make_llm  # noqa: E402

VERDICTS = ['ok', 'gold_wrong', 'gold_incomplete', 'gold_extra', 'question_ambiguous',
            'label_answerability', 'evidence_mismatch']

QUESTION_PROMPT = """You are auditing a benchmark for long-term memory of industrial dialogue agents. \
Below is the complete dialogue of ONE manufacturing scenario (5 sessions), the plant's shift calendar, \
and one benchmark question with its gold answer. Decide whether the gold label is right.

Rules for judging:
- Judge only against the dialogue. The gold must be what a careful reader of this dialogue would answer at time as_of.
- "gold_wrong": the gold contradicts the dialogue, or names the wrong state / time / shift / role / value.
- "gold_incomplete": the question asks for something the gold leaves out (e.g. asks for two items, gold gives one).
- "gold_extra": the gold asserts details that are not in the dialogue or that the question does not ask for, \
in a way that would make a correct answer be marked wrong.
- "question_ambiguous": the question admits more than one reasonable answer given the dialogue.
- "label_answerability": the question is labeled answerable but the dialogue does not contain the answer, \
or labeled unanswerable but the dialogue does contain it.
- "evidence_mismatch": the gold is fine but the cited evidence turns do not contain the information.
- "ok": none of the above. Wording differences and harmless paraphrase are "ok".
Pick the single most serious verdict. severity: "major" if a correct system would likely be scored wrong \
(or a wrong one scored right), else "minor".

Shift calendar: {calendar}

Dialogue:
{dialogue}

as_of: {as_of}
Question ({category}, labeled {answerable}): {question}
Gold answer: {reference}
Cited evidence turn ids: {evidence}

Reply with JSON only:
{{"verdict": "<one of {verdicts}>", "severity": "none|minor|major", "explanation": "<1-3 sentences>", \
"suggested_gold": "<corrected gold, or empty if ok>"}}"""

SCENARIO_PROMPT = """You are auditing a synthetic benchmark of industrial (manufacturing) dialogue between \
workers and an industrial AI agent that calls plant tools. Read the complete dialogue of ONE scenario and list \
every problem a domain expert or a paper reviewer would flag:
- contradiction: two turns disagree on a number, reading, state, time, entity or outcome \
(e.g. a reading reported normal in one session but described as abnormal before the fix in another);
- role_inconsistency: a speaker says something their role could not know or would not say;
- implausible: industrially unrealistic content (wrong units, impossible values, fix unrelated to fault, \
tool returning information it could not measure);
- incoherent: turns that do not follow from each other (answers to questions never asked, outcomes stated \
before they happen).
Ignore stylistic repetition and the terse filler lines ("收到", "OK") -- they are intentional low-density turns.

Shift calendar: {calendar}

Dialogue:
{dialogue}

Reply with JSON only:
{{"issues": [{{"type": "contradiction|role_inconsistency|implausible|incoherent", "severity": "minor|major", \
"turn_ids": ["..."], "explanation": "<1-2 sentences>"}}]}}  (empty list if none)"""


def render_dialogue(sessions):
    lines = []
    for s in sessions:
        lines.append(f"=== {s['session_id']} (start {s['session_start']}) ===")
        for t in s['turns']:
            tool = f" [{t['tool_name']}]" if t.get('tool_name') else ''
            lines.append(f"{t['dialog_id']} | {t['timestamp']} | {t['speaker_role']}/{t['turn_type']}{tool} | {t['text']}")
    return '\n'.join(lines)


def parse_json(text):
    m = re.search(r'\{.*\}', text, re.S)
    try:
        return json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--lang', default='zh')
    p.add_argument('--splits', nargs='+', default=['test', 'test_ext'])
    p.add_argument('--model', default='gpt-6-luna')
    p.add_argument('--base-url', default='https://api.openai.com/v1')
    p.add_argument('--api-key-env', default='JUDGE_API_KEY')
    p.add_argument('--workers', type=int, default=8)
    p.add_argument('--limit', type=int, default=0, help='audit only the first N scenarios (smoke test)')
    p.add_argument('--categories', nargs='+', default=None, help='question-audit only these categories')
    p.add_argument('--q-scenarios', type=int, default=0,
                   help='question-audit only the first N scenarios (all selected scenarios still get the scenario audit)')
    p.add_argument('--out', default=str(ROOT / 'results' / 'data_audit'))
    p.add_argument('--cache', default=str(ROOT / '.cache' / 'data_audit.sqlite'))
    a = p.parse_args()

    src = ROOT / 'data' / DATA_DIRS[a.lang]
    cal = json.loads((src / 'production_calendar.json').read_text())
    memory_text = {m['memory_id']: m['statement'] for sp in SPLITS for m in read_jsonl(src / sp / 'memory_units.jsonl')}
    wm = {w['scenario_id']: w for sp in SPLITS for w in read_jsonl(src / sp / 'working_memory.jsonl')}
    scen = [s for sp in a.splits for s in read_jsonl(src / sp / 'scenarios.jsonl')]
    if a.limit:
        scen = scen[:a.limit]
    keep = {s['scenario_id'] for s in scen}
    sessions, questions = defaultdict(list), []
    for sp in a.splits:
        for s in read_jsonl(src / sp / 'sessions.jsonl'):
            if s['scenario_id'] in keep:
                sessions[s['scenario_id']].append(s)
        questions += [q for q in read_jsonl(src / sp / 'questions.jsonl') if q['scenario_id'] in keep]
    if a.categories:
        questions = [q for q in questions if q['category'] in a.categories]
    if a.q_scenarios:
        q_keep = {s['scenario_id'] for s in scen[:a.q_scenarios]}
        questions = [q for q in questions if q['scenario_id'] in q_keep]
    site = {s['scenario_id']: s['site_id'] for s in scen}

    def calendar(sid):
        c = cal['site_calendar'][site[sid]]
        return json.dumps(cal['calendars'][c], ensure_ascii=False)

    dialogue = {sid: render_dialogue(sorted(ss, key=lambda s: s['session_id'])) for sid, ss in sessions.items()}
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tag = f"{a.lang}_{'+'.join(a.splits)}"
    q_path, s_path = out / f'gold_audit_{tag}.jsonl', out / f'scenario_audit_{tag}.jsonl'
    done_q = {json.loads(x)['question_id'] for x in q_path.read_text().splitlines()} if q_path.exists() else set()
    done_s = {json.loads(x)['scenario_id'] for x in s_path.read_text().splitlines()} if s_path.exists() else set()

    llm = make_llm(a.model, a.base_url, a.api_key_env, cache_path=a.cache, max_tokens=1024)
    lock = threading.Lock()

    def ask(prompt, stage):
        for attempt in range(3):
            c = llm.complete([{'role': 'user', 'content': prompt}], stage=stage,
                             **({'temperature': 0.0} if attempt == 0 else {'temperature': 0.3}))
            r = parse_json(c.text)
            if r is not None:
                return r, c.text
        return None, c.text

    def audit_q(q):
        prompt = QUESTION_PROMPT.format(
            calendar=calendar(q['scenario_id']), dialogue=dialogue[q['scenario_id']], as_of=q['as_of'],
            category=q['category'], answerable='answerable' if q['answerable'] else 'UNANSWERABLE',
            question=q['question'], reference=_reference_text(q, a.lang, memory_text, wm),
            evidence=', '.join(q['evidence']) or '(none)', verdicts='|'.join(VERDICTS))
        r, raw = ask(prompt, 'audit_question')
        return {'question_id': q['question_id'], 'scenario_id': q['scenario_id'], 'category': q['category'], 'model': a.model,
                'question': q['question'], 'reference': _reference_text(q, a.lang, memory_text, wm),
                **(r or {'verdict': 'parse_error', 'raw': raw})}

    def audit_s(sid):
        r, raw = ask(SCENARIO_PROMPT.format(calendar=calendar(sid), dialogue=dialogue[sid]), 'audit_scenario')
        return {'scenario_id': sid, 'model': a.model, **(r or {'issues': None, 'raw': raw})}

    jobs = [(audit_s, sid, s_path) for sid in sorted(dialogue) if sid not in done_s] + \
           [(audit_q, q, q_path) for q in questions if q['question_id'] not in done_q]
    print(f'{len(jobs)} audit calls ({len(done_s)} scenarios / {len(done_q)} questions already done)', flush=True)
    with ThreadPoolExecutor(a.workers) as ex:
        futs = [ex.submit(fn, arg) for fn, arg, _ in jobs]
        paths = {f: path for f, (_, _, path) in zip(futs, jobs)}
        for i, f in enumerate(as_completed(futs), 1):
            row = f.result()
            with lock, open(paths[f], 'a') as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + '\n')
            if i % 200 == 0:
                print(f'{i}/{len(jobs)}', flush=True)

    rows = [json.loads(x) for x in q_path.read_text().splitlines()]
    srows = [json.loads(x) for x in s_path.read_text().splitlines()]
    by_cat = defaultdict(Counter)
    for r in rows:
        by_cat[r['category']][(r.get('verdict'), r.get('severity'))] += 1
    summary = {
        'questions': len(rows),
        'verdicts': dict(Counter(r.get('verdict') for r in rows)),
        'major': sum(r.get('severity') == 'major' for r in rows),
        'major_by_category': {c: sum(v for (vd, sv), v in cnt.items() if sv == 'major') for c, cnt in sorted(by_cat.items())},
        'scenarios': len(srows),
        'scenario_issues': dict(Counter(f"{i.get('type')}/{i.get('severity')}" for r in srows for i in (r.get('issues') or []))),
        'scenarios_with_major_issue': sum(any(i.get('severity') == 'major' for i in (r.get('issues') or [])) for r in srows),
    }
    (out / f'summary_{tag}.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1))
    print(json.dumps(summary, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
