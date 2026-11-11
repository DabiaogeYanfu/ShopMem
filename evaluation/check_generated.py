"""Semantic consistency checks for the generated corpus (v0.2 shift / anaphora / unanswerable).

validate_dataset.py checks required fields and cross-file references; this script checks
that the generated content means what its labels claim:
- turn timestamps are strictly increasing and a session never crosses a shift boundary;
- every resolved_shift matches the site calendar, lies in the session's current or
  previous shift, and precedes the utterance that refers to it;
- (v0.4) quoted tool measurement times never come after the user's report: in phases 0/2/3
  they equal the shift-anchored occurrence of the report, in phase 4 they precede the
  session; the 72-hour verification starts after the fix measurement;
- (v0.4) routine "normal" tool calls never query the scenario's open problem, the domain
  owner reports the problem and asks to close the task, and lot numbers encode the month
  of the first report;
- look-alike IDs of unseen-entity questions never appear in any dialogue, and the
  attributes asked by unrecorded-attribute questions are never mentioned;
- shift_temporal answers agree with the resolved_shift of their evidence turn;
- working-memory constraints and open items are stated in the dialogue, and
  constraint_recall evidence contains every constraint;
- evidence turns are never labeled low density;
- no question text repeats within a scenario.

Usage: python evaluation/check_generated.py [--lang zh|en]
"""
import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'generation'))

LANGS = {
    # v0.5: the measurement time is the `measured_at` field of the JSON tool observation
    'zh': ('generate_dataset', 'generated', r'"measured_at": "(\d{4}-\d\d-\d\d \d\d:\d\d)"',
           ['供应商', '费用', '客户', '第二次失败', '停线', '第二个故障部件']),  # v0.5 false premises
    'en': ('generate_dataset_en', 'generated-EN', r'"measured_at": "(\d{4}-\d\d-\d\d \d\d:\d\d)"',
           ['supplier', 'cost', 'customer', 'second failed corrective', 'line stop', 'second faulty part']),
}
LOOKALIKE = re.compile(r'[A-Z][A-Z0-9\-]*-\d{3}|LOT\d{6}-\d{3}')


def check(lang):
    module, data_dir, ts_pattern, unrecorded_words = LANGS[lang]
    gen = __import__(module)
    d = ROOT / 'data' / data_dir
    read = lambda name: [json.loads(x) for sp in ['train', 'dev', 'test', 'dev_ext', 'test_ext']
                         for x in (d / sp / name).read_text().splitlines() if x.strip()]
    sessions, questions = read('sessions.jsonl'), read('questions.jsonl')
    cal_of = {s['scenario_id']: s['production_calendar_id'] for s in read('scenarios.jsonl')}
    wm_of = {w['scenario_id']: w for w in read('working_memory.jsonl')}
    turn_by_id = {t['dialog_id']: t for s in sessions for t in s['turns']}
    all_text = '\n'.join(t['text'] for t in turn_by_id.values())
    errors, counts = Counter(), Counter()
    scen_of = {s['scenario_id']: s for s in read('scenarios.jsonl')}
    by_scen = {}
    for s in sessions:
        by_scen.setdefault(s['scenario_id'], []).append(s)
    owner_role = {dom: dict(gen.ROLES)[r] for dom, r in gen.DOMAIN_OWNER.items()}

    for sid, ss in by_scen.items():
        ss = sorted(ss, key=lambda s: s['session_id'])
        dom = scen_of[sid]['domain']
        family = gen.MAINT_FAMILY.get(next((m for m in gen.MAINT_FAMILY if any(m in t['text'] for s in ss for t in s['turns'])), ''), {})
        banned = gen.PROBLEM_TOOLS[dom] | ({family['tool']} if family else set())
        measured = {}
        for n, s in enumerate(ss, 1):
            first_uq = next((t for t in s['turns'] if t['turn_type'] in ('user_query', 'human') and not t['is_low_density']), None)
            if n in (1, 5):
                errors['report_not_by_domain_owner'] += first_uq is None or first_uq['speaker_role'] != owner_role[dom]
            for j, t in enumerate(s['turns']):
                if t['turn_type'] == 'tool_observation' and t['is_low_density']:
                    errors['routine_check_queries_open_problem'] += t.get('tool_name') in banned
                if t['turn_type'] in ('agent_tool_call', 'tool_observation'):
                    try:
                        obj = json.loads(t['text'])
                        errors['tool_turn_bad_json'] += obj.get('tool') != t.get('tool_name')
                    except json.JSONDecodeError:
                        errors['tool_turn_bad_json'] += 1
                if t['turn_type'] == 'tool_observation':
                    errors['observation_without_call'] += j == 0 or s['turns'][j - 1]['turn_type'] != 'agent_tool_call'
                m = re.search(ts_pattern, t['text']) if t['turn_type'] == 'tool_observation' and not t['is_low_density'] else None
                if m:
                    measured[n] = datetime.fromisoformat(m.group(1).replace(' ', 'T') + '+08:00')
                    errors['measurement_after_report'] += measured[n] > datetime.fromisoformat(first_uq['timestamp'])
                    if first_uq.get('resolved_shift'):
                        errors['measurement_not_at_occurrence'] += measured[n] != datetime.fromisoformat(first_uq['resolved_time']).replace(second=0)
        if 4 in measured and 5 in measured:
            errors['verification_within_72h_of_fix'] += measured[5] - measured[4] < timedelta(hours=72)
        lot_month = re.search(r'LOT(\d{6})-\d{3}', ' '.join(t['text'] for s in ss for t in s['turns']))
        if lot_month:
            errors['lot_month_not_first_report'] += lot_month.group(1) != ss[0]['session_start'][:7].replace('-', '')

    for s in sessions:
        cal = cal_of[s['scenario_id']]
        shift_id, shift_st, shift_en = gen.shift_of(datetime.fromisoformat(s['session_start']), cal)
        prev_id = gen.shift_of(shift_st - timedelta(minutes=1), cal)[0]
        times = [datetime.fromisoformat(t['timestamp']) for t in s['turns']]
        errors['non_monotonic_session'] += any(b <= a for a, b in zip(times, times[1:]))
        errors['session_crosses_shift'] += times[-1] >= shift_en
        for j, t in enumerate(s['turns']):
            if 'resolved_shift' in t:
                counts['resolved_shift_turns'] += 1
                rt = datetime.fromisoformat(t['resolved_time'])
                errors['resolved_shift_mismatch'] += gen.shift_of(rt, cal)[0] != t['resolved_shift']
                errors['resolved_shift_not_cur_or_prev'] += t['resolved_shift'] not in (shift_id, prev_id)
                errors['occurrence_not_before_utterance'] += rt >= times[j]
            if 'resolved_entities' in t:
                counts['resolved_entity_turns'] += 1
            for m in re.finditer(ts_pattern, t['text']):
                counts['quoted_detection_times'] += 1

    for q in questions:
        counts[f"q_{q['category']}"] += 1
        errors['evidence_low_density'] += any(turn_by_id[e]['is_low_density'] for e in q['evidence'])
        if q['category'] == 'shift_temporal':
            ev = turn_by_id[q['evidence'][0]]
            errors['shift_answer_mismatch'] += ev.get('resolved_shift') != q['answer']['aux']['shift_id']
        if q['category'] == 'unanswerable':
            kind = q['answer']['aux']['unanswerable_type']
            errors['unanswerable_has_evidence'] += bool(q['evidence']) or q['answerable']
            if kind == 'unseen_entity':
                ids = LOOKALIKE.findall(q['question'])
                errors['unseen_entity_missing_or_leaked'] += not ids or any(i in all_text for i in ids)
        if q['category'] == 'constraint_recall':
            ev_text = ' '.join(turn_by_id[e]['text'] for e in q['evidence'])
            wm = wm_of[q['scenario_id']]
            errors['constraint_not_in_evidence'] += any(c not in ev_text for c in wm['constraints'])
    errors['duplicate_question_in_scenario'] = len(questions) - len({(q['scenario_id'], q['question']) for q in questions})
    # every constraint / open item of a working-memory snapshot must be stated in the dialogue
    for wm in wm_of.values():
        ev_text = ' '.join(turn_by_id[d]['text'] for d in wm['source_dialog_ids'])
        items = wm['constraints'] + [p['description'] for p in wm['pending_items']]
        errors['working_memory_item_not_in_dialogue'] += sum(x not in ev_text for x in items)
    for w in unrecorded_words:
        errors[f'unrecorded_word_in_dialogue:{w}'] += len(re.findall(rf'\b{w}' if lang == 'en' else w, all_text, re.I))
    return {'counts': dict(counts), 'errors': {k: v for k, v in errors.items() if v}}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--lang', choices=list(LANGS), nargs='+', default=list(LANGS))
    args = p.parse_args()
    results = {lang: check(lang) for lang in args.lang}
    print(json.dumps(results, ensure_ascii=False, indent=2))
    raise SystemExit(1 if any(r['errors'] for r in results.values()) else 0)
