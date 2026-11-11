"""Load M-LoCoMo-Long as a memory system sees it, with the gold side kept apart.

Memory methods only ever receive `History` and `Query` objects. They carry what an
industrial agent's own interaction log would contain: text, speaker, timestamp, message
type (turn_type / tool_name) and site. Everything else stays in `Gold`, which only the
scorer and explicitly-oracle baselines may read:
- ground-truth labels (is_low_density, resolved_*, mentions);
- scenario membership. Original session / dialog / speaker IDs encode the scenario number
  (SC_GEN_0003_S01_D02, eng0003, AGENT_0003), which would let a method group sessions by
  incident or resolve "it" from an ID suffix. Each history therefore gets opaque turn IDs
  (T00001...), opaque session IDs, one shared AGENT id, per-system tool IDs and randomly
  numbered people; `Gold.original_dialog_id` maps back for scoring.
"""
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
DATA_DIRS = {'zh': 'generated', 'en': 'generated-EN'}
SPLITS = ['train', 'dev', 'test', 'dev_ext', 'test_ext']


@dataclass(frozen=True)
class Turn:
    dialog_id: str
    session_id: str
    site_id: str
    speaker_id: str
    speaker_role: str
    timestamp: str
    text: str
    turn_type: str
    tool_name: Optional[str] = None


@dataclass(frozen=True)
class Session:
    session_id: str
    session_start: str
    site_id: str
    turns: tuple


@dataclass(frozen=True)
class History:
    history_id: str
    lang: str
    K: int
    sessions: tuple

    def turns(self):
        return [t for s in self.sessions for t in s.turns]


@dataclass(frozen=True)
class Query:
    """What a memory method is asked: no answer, evidence or category."""
    question_id: str
    history_id: str
    text: str
    as_of: str


@dataclass(frozen=True)
class Question:
    question_id: str
    history_id: str
    question: str
    category: str
    answerable: bool
    as_of: str
    reference: str
    evidence: tuple          # opaque turn ids
    required_events: tuple
    aux: dict

    def query(self):
        return Query(self.question_id, self.history_id, self.question, self.as_of)


@dataclass
class Gold:
    # Opaque turn ids restart at T00001 in every history, so turn-keyed gold is kept per
    # history: one flat dict let later histories overwrite earlier ones (until 2026-09-24 the
    # oracle baselines read another history's labels whenever a pool had several histories).
    low_density: dict            # history id -> {opaque turn id -> bool}
    event_turns: dict            # event id -> tuple of opaque turn ids
    original_dialog_id: dict     # history id -> {opaque turn id -> original dialog id}
    calendars: dict              # production_calendar.json


def read_jsonl(path):
    return [json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]


def _speaker_alias(raw_id, role, people):
    if role == 'industrial_agent':
        return 'AGENT'
    if role == 'system_tool':
        return raw_id.rsplit('_', 1)[0]  # SYS_EMS_0003 -> SYS_EMS
    return people[raw_id]


def _anonymize(raw_history, lang, rng):
    """Opaque ids for one history; returns the History and old->new turn id map."""
    hid = raw_history['history_id']
    people = {}
    for s in raw_history['sessions']:
        for t in s['turns']:
            if t['speaker_role'] not in ('industrial_agent', 'system_tool'):
                people.setdefault(t['speaker_id'], None)
    numbers = rng.sample(range(100, 1000), len(people))
    people = {pid: f'P{n}' for pid, n in zip(sorted(people), numbers)}
    id_map, sessions, n = {}, [], 0
    for si, s in enumerate(raw_history['sessions'], 1):
        sid = f'S{si:04d}'
        turns = []
        for t in s['turns']:
            n += 1
            new_id = f'T{n:05d}'
            id_map[t['dialog_id']] = new_id
            turns.append(Turn(dialog_id=new_id, session_id=sid, site_id=s['site_id'],
                              speaker_id=_speaker_alias(t['speaker_id'], t['speaker_role'], people),
                              speaker_role=t['speaker_role'], timestamp=t['timestamp'], text=t['text'],
                              turn_type=t['turn_type'], tool_name=t.get('tool_name')))
        sessions.append(Session(sid, s['session_start'], s['site_id'], tuple(turns)))
    return History(hid, lang, raw_history['K'], tuple(sessions)), id_map


def _reference_text(q, lang, memory_text, wm_by_scenario):
    ans = q['answer']
    if ans['type'] == 'text':
        return ans['reference']
    # working_memory_snapshot: render the gold snapshot as text for the judge
    wm = wm_by_scenario[q['scenario_id']]
    facts = [memory_text[m] for m in ans['active_memory_ids']]
    pending = [p['description'] for p in wm['pending_items'] if p['item_id'] in ans.get('pending_item_ids', [])]
    zh = lang == 'zh'
    parts = [(('最新状态：' if zh else 'Latest state: ') + ('；' if zh else ' ').join(facts)) if facts else '',
             (('待办：' if zh else 'Open items: ') + ('；' if zh else '; ').join(pending) + ('。' if zh else '.')) if pending else '',
             (('约束：' if zh else 'Constraints: ') + ('；' if zh else '; ').join(ans['constraints']) + ('。' if zh else '.'))
             if ans['constraints'] else '']
    return ' '.join(p for p in parts if p)


def load(lang, pool, K, root=ROOT, seed=0):
    """Histories, questions and gold for one (lang, pool, K) setting of M-LoCoMo-Long."""
    d = root / 'data' / 'long' / lang / pool / f'K{K:03d}'
    if not d.exists():
        raise FileNotFoundError(f'{d} missing -- run generation/build_long_history.py')
    src = root / 'data' / DATA_DIRS[lang]
    events = [e for sp in SPLITS for e in read_jsonl(src / sp / 'events.jsonl')]
    memory_text = {m['memory_id']: m['statement'] for sp in SPLITS for m in read_jsonl(src / sp / 'memory_units.jsonl')}
    wm_by_scenario = {w['scenario_id']: w for sp in SPLITS for w in read_jsonl(src / sp / 'working_memory.jsonl')}
    calendars = json.loads((src / 'production_calendar.json').read_text())

    rng = random.Random(seed)
    histories, id_map, low_density, original = {}, {}, {}, {}
    for raw in read_jsonl(d / 'histories.jsonl'):
        h, m = _anonymize(raw, lang, rng)
        histories[h.history_id] = h
        id_map.update(m)  # original dialog ids are unique corpus-wide, so this map never collides
        original[h.history_id] = {v: k for k, v in m.items()}
        low_density[h.history_id] = {m[t['dialog_id']]: t['is_low_density'] for s in raw['sessions'] for t in s['turns']}
    event_turns = {e['event_id']: tuple(id_map[x] for x in e['source_dialog_ids'] if x in id_map) for e in events}
    questions = []
    for q in read_jsonl(d / 'questions.jsonl'):
        questions.append(Question(
            question_id=q['question_id'], history_id=q['history_id'], question=q['question'],
            category=q['category'], answerable=q.get('answerable', True), as_of=q['as_of'],
            reference=_reference_text(q, lang, memory_text, wm_by_scenario),
            evidence=tuple(id_map[e] for e in q['evidence']), required_events=tuple(q['required_events']),
            aux=q['answer'].get('aux', {})))
    gold = Gold(low_density=low_density, event_turns=event_turns, original_dialog_id=original, calendars=calendars)
    return list(histories.values()), questions, gold
