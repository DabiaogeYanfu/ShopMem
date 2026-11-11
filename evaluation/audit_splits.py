"""Split-parity and structural audit of the generated corpus (layers 1-3 of the data audit).

Compares every split against train on distributional statistics, re-derives structural
invariants that check_generated.py does not cover, and measures how novel each split's
dialogue is relative to train once entity display names are masked out.

Usage: python evaluation/audit_splits.py [--lang zh|en] [--out results/data_audit]
"""
import argparse
import json
import re
import statistics as st
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRS = {'zh': 'generated', 'en': 'generated-EN'}
SPLITS = ['train', 'dev', 'test', 'dev_ext', 'test_ext']
# display names end in a 3-digit serial (e.g. CA6140-M1-601, LOT202601-601, 温度传感器#601);
# alarms are ALARM_<n>, raw ids X_0001 -- mask all of them to compare templates across scenarios
MASK = [(re.compile(r'[A-Za-z0-9一-鿿-]*[A-Za-z一-鿿][A-Za-z0-9一-鿿-]*[-#] ?\d{3}\b'), '<ENT>'), (re.compile(r'\b[A-Z]+(_[A-Z]+)*_\d+\b'), '<ID>'),
        (re.compile(r'\d+(\.\d+)?'), '<N>')]


def read(lang, split, name):
    return [json.loads(x) for x in (ROOT / 'data' / DIRS[lang] / split / f'{name}.jsonl').read_text().splitlines() if x.strip()]


def mask(text):
    for pat, rep in MASK:
        text = pat.sub(rep, text)
    return text


def shift_of(t, cal, calendars):
    for day in (t.date() - timedelta(days=1), t.date()):
        for sh in calendars[cal]:
            hh, mm = map(int, sh['start'].split(':'))
            s = datetime(day.year, day.month, day.day, hh, mm, tzinfo=t.tzinfo)
            if s <= t < s + timedelta(hours=sh['hours']):
                return f"{day.isoformat()} {sh['shift']}"
    return None


def audit(lang):
    cal = json.loads((ROOT / 'data' / DIRS[lang] / 'production_calendar.json').read_text())
    data = {sp: {n: read(lang, sp, n) for n in ['scenarios', 'sessions', 'questions']} for sp in SPLITS}
    train_templates = Counter(mask(t['text']) for s in data['train']['sessions'] for t in s['turns'])
    train_questions = Counter(mask(q['question']) for q in data['train']['questions'])
    stats, errors = {}, defaultdict(list)
    seen_dialog, seen_display = {}, {}
    for sp in SPLITS:
        scen, sess, qs = data[sp]['scenarios'], data[sp]['sessions'], data[sp]['questions']
        site = {s['scenario_id']: s['site_id'] for s in scen}
        turns = [(s, t) for s in sess for t in s['turns']]
        by_scen = defaultdict(list)
        for s, t in turns:
            by_scen[s['scenario_id']].append(t)
        # structural invariants
        for s in sess:
            ts = [t['timestamp'] for t in s['turns']]
            if ts != sorted(ts):
                errors[sp].append(f"{s['session_id']}: turns not chronological")
            if s['session_start'] > ts[0]:
                errors[sp].append(f"{s['session_id']}: session_start after first turn")
            if shift_of(datetime.fromisoformat(s['session_start']), cal['site_calendar'][site[s['scenario_id']]], cal['calendars']) is None:
                errors[sp].append(f"{s['session_id']}: start outside calendar")
        for s, t in turns:
            if t['dialog_id'] in seen_dialog:
                errors[sp].append(f"dialog_id {t['dialog_id']} also in {seen_dialog[t['dialog_id']]}")
            seen_dialog[t['dialog_id']] = sp
            if t.get('resolved_time') and t.get('resolved_shift'):
                want = shift_of(datetime.fromisoformat(t['resolved_time']), cal['site_calendar'][site[s['scenario_id']]], cal['calendars'])
                if want != t['resolved_shift']:
                    errors[sp].append(f"{t['dialog_id']}: resolved_shift {t['resolved_shift']} != calendar {want}")
            if t.get('resolved_time') and t['resolved_time'] > t['timestamp']:
                errors[sp].append(f"{t['dialog_id']}: resolved_time after utterance")
        # display names (masked-out entities) must belong to exactly one scenario corpus-wide
        for sid, ts in by_scen.items():
            for name in {m.group(0) for t in ts for m in MASK[0][0].finditer(t['text'])}:
                if seen_display.setdefault(name, sid) != sid:
                    errors[sp].append(f"display name {name} in {sid} and {seen_display[name]}")
        ids_by_scen = {sid: {t['dialog_id'] for t in ts} for sid, ts in by_scen.items()}
        for q in qs:
            if not set(q['evidence']) <= ids_by_scen[q['scenario_id']]:
                errors[sp].append(f"{q['question_id']}: evidence outside its scenario")
            if q['answerable'] != bool(q['evidence']):
                errors[sp].append(f"{q['question_id']}: answerable={q['answerable']} but {len(q['evidence'])} evidence")
        dup_q = sum(c - 1 for c in Counter(q['question'] for q in qs).values() if c > 1)
        own = [t for _, t in turns]
        ld = Counter((t['turn_type'], t['is_low_density']) for t in own)
        types = sorted({t['turn_type'] for t in own})
        stats[sp] = {
            'scenarios': len(scen),
            'domains': dict(Counter(s['domain'] for s in scen)),
            'sites': dict(Counter(s['site_id'] for s in scen)),
            'sessions': len(sess),
            'turns_per_scenario': round(len(own) / len(scen), 2),
            'chars_per_scenario': round(sum(len(t['text']) for t in own) / len(scen), 1),
            'low_density_ratio': round(sum(t['is_low_density'] for t in own) / len(own), 4),
            'low_density_by_type': {ty: round(ld[(ty, True)] / max(1, ld[(ty, True)] + ld[(ty, False)]), 3) for ty in types},
            'turns_with_resolved_time': sum(bool(t.get('resolved_time')) for t in own),
            'turns_with_resolved_shift': sum(bool(t.get('resolved_shift')) for t in own),
            'session_start_shift': dict(Counter(shift_of(datetime.fromisoformat(s['session_start']), cal['site_calendar'][site[s['scenario_id']]], cal['calendars']).split(' ')[1] for s in sess)),
            'questions': len(qs),
            'questions_by_category': dict(Counter(q['category'] for q in qs)),
            'evidence_per_answerable_q': round(st.mean(len(q['evidence']) for q in qs if q['answerable']), 3),
            'duplicate_question_strings': dup_q,
            'distinct_masked_turn_templates': len({mask(t['text']) for t in own}),
            'turn_template_seen_in_train': round(sum(mask(t['text']) in train_templates for t in own) / len(own), 4),
            'question_template_seen_in_train': round(sum(mask(q['question']) in train_questions for q in qs) / len(qs), 4),
        }
    return stats, {k: v for k, v in errors.items()}


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--langs', nargs='+', default=['zh', 'en'])
    p.add_argument('--out', default=str(ROOT / 'results' / 'data_audit'))
    a = p.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for lang in a.langs:
        stats, errors = audit(lang)
        (out / f'split_parity_{lang}.json').write_text(json.dumps({'stats': stats, 'errors': errors}, ensure_ascii=False, indent=1))
        keys = [k for k in stats['train'] if not isinstance(stats['train'][k], dict)]
        print(f'## {lang}')
        print('| metric | ' + ' | '.join(SPLITS) + ' |')
        for k in keys:
            print(f'| {k} | ' + ' | '.join(str(stats[sp][k]) for sp in SPLITS) + ' |')
        for k in ['low_density_by_type', 'session_start_shift', 'domains']:
            for sp in SPLITS:
                print(f'  {k}[{sp}] = {stats[sp][k]}')
        print('errors:', {sp: (len(e), e[:5]) for sp, e in errors.items()} or 'none')
