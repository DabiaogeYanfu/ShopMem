"""Tests for the v2 Writer pieces: windows, window -> question map, memory ops, r_ind, ShopMem-Writer.

    .venv/bin/python -m pytest tests -q
"""
import json
import re
from datetime import datetime
from types import SimpleNamespace

import pytest

from memeval.data import ROOT, Turn, read_jsonl, load
from memeval.evidence_map import window_map
from memeval.llm import LLM, DryRunLLM
from memeval.memory_ops import (END_MARK, Action, Fact, MemoryBank, entity_type, parse_action, parse_fact,
                                parse_lines)
from memeval.methods import METHODS
from memeval.methods.shopmem import ShiftCalendar, ShopMem
from memeval.methods.shopmem_writer_prompts import EXAMPLE_FACTS, EXAMPLE_SITE, EXAMPLE_WINDOW
from memeval.rind import check_window, expected_shift
from memeval.windows import Window, session_windows

CAL = ShiftCalendar(json.loads((ROOT / 'data/generated-EN/production_calendar.json').read_text()))
_LINE = re.compile(r'^\[(.*?)\] (\S+) \((\w+), (\w+)(?::(\w+))?\): (.*)$')


@pytest.fixture(scope='module')
def dev30():
    return load('en', 'dev30', 30)


def make_turns(lines, session='S0001', site=EXAMPLE_SITE):
    out = []
    for i, line in enumerate(lines, 1):
        ts, speaker, role, kind, tool, text = _LINE.match(line).groups()
        out.append(Turn(f'T{i:05d}', session, site, speaker, role, ts, text, kind, tool))
    return out


def example_window(history=()):
    turns = make_turns(EXAMPLE_WINDOW)
    return Window(0, 'S0001', EXAMPLE_SITE, tuple(turns), tuple(history), tuple(t.dialog_id for t in turns))


def example_facts(w):
    return [parse_fact(f, w.turns) for f in EXAMPLE_FACTS]


# ---------------------------------------------------------------- windows and the evidence map
def test_windows_match_v1_cut(dev30):
    histories, _, _ = dev30
    h = histories[0]
    v1 = ShopMem(DryRunLLM(), 'en')
    v1._cfg()
    old = [[t.dialog_id for t in w['turns']] for w in v1._windows(h)]
    new = session_windows(h)
    assert old == [w.turn_ids for w in new]
    assert [t.dialog_id for t in h.turns()] == [x for w in new for x in w.new_turns]  # overlap counted once


def test_window_map_attaches_to_last_evidence_window(dev30):
    histories, questions, gold = dev30
    h = histories[0]
    rows, unattached = window_map(h, questions, gold.low_density[h.history_id])
    by_q = {q.question_id: q for q in questions}
    first = {t: r['window'] for r in rows for t in r['new_turns']}
    attached = [qid for r in rows for qid in r['questions']]
    assert len(attached) == len(set(attached)) and len(attached) + len(unattached) == len(questions)
    assert all(by_q[q].category == 'unanswerable' and not by_q[q].evidence for q in unattached)
    for r in rows:
        for qid in r['questions']:
            wins = [first[e] for e in by_q[qid].evidence]
            assert max(wins) == r['window']     # the window of the last evidence turn, where it first appears
        if r['role'] == 'noop':
            assert not r['evidence_turns'] and r['n_low_density'] == r['n_new']
    assert {e for r in rows for e in r['evidence_turns']} == {e for q in questions for e in q.evidence}


# ---------------------------------------------------------------- memory ops
def test_parse_lines_formats():
    assert parse_lines('{"a": 1}\n{"a": 2}\n' + END_MARK) == ([{'a': 1}, {'a': 2}], True, 0)
    assert parse_lines('```json\n[{"a": 1}]\n```\n' + END_MARK) == ([{'a': 1}], True, 0)
    objs, complete, bad = parse_lines('{"a": 1}\n{"a": 2, "b": "cut o')
    assert objs == [{'a': 1}] and not complete and bad == 1
    assert parse_lines(END_MARK) == ([], True, 0)


def test_entity_types():
    assert entity_type('PRESS-LINE-07-095') == 'Equipment' and entity_type('CA6136-M5-653') == 'Equipment'
    assert entity_type('LOT202509-002') == 'MaterialLot' and entity_type('SP-4430') == 'MaterialLot'
    assert entity_type('WO_0095') == 'WorkOrder' and entity_type('ALARM_373') == 'Alarm'
    assert entity_type('A-03-06') == 'StorageLocation' and entity_type('temperature sensor #081') == 'Component'
    assert entity_type('sub-lot tag #304') == 'Component' and entity_type('SPINDLE_OVERTEMP') == 'AlarmCategory'
    assert entity_type('#316') is None and entity_type('inspection SOP') is None
    assert entity_type('the spindle') is None and entity_type('Caroline') is None


def test_parse_fact_maps_window_turns():
    w = example_window()
    f = parse_fact({'content': 'x', 'entities': 'CNC-M2-915', 'turns': ['#2', 5, 99], 'source': 'Tool',
                    'time': 'null'}, w.turns)
    assert f.turns == ['T00002', 'T00005'] and f.bad_turns == [99] and f.entities == ['CNC-M2-915']
    assert f.source == 'tool' and f.time is None
    assert parse_fact({'content': ' '}, w.turns) is None
    assert parse_action({'fact': 'F2', 'action': 'supersede', 'old': 'M3'}) == Action('SUPERSEDE', 2, 'M3')


def _unit(f, t='2026-03-10T09:00:00+08:00'):
    t = datetime.fromisoformat(t)
    return {'content': f.content, 'entities': f.entities, 'time': t, 'shift': None, 'source': f.source,
            'source_turns': f.turns, 'session_id': 'S1', 'record_time': t, 'valid_from': t}


def test_bank_apply_semantics():
    bank = MemoryBank()
    f1, f2, f3 = Fact('state A', ['EQ-1-001']), Fact('state B', ['EQ-1-001']), Fact('constraint', ['WO_0001'])
    now = datetime.fromisoformat('2026-03-10T10:00:00+08:00')
    bank.apply([f1, f3], [Action('ADD', 1)], _unit, now)          # fact 2 has no action: added anyway
    assert [u.uid for u in bank.units] == ['M1', 'M2']
    later = lambda f: _unit(f, '2026-03-11T09:00:00+08:00')
    applied, errors = bank.apply([f2], [Action('SUPERSEDE', 1, 'M1'), Action('EXPIRE', old='M2'),
                                        Action('EXPIRE', old='M2')], later, now)
    m1, m2, m3 = bank.units
    assert not m1.valid and m1.closed_by == 'M3' and m1.valid_to == m3.valid_from and m3.valid
    assert m2.closed_by == 'EXPIRE' and m2.valid_to == now
    assert errors == [('bad_target', 'M2')]                          # a closed unit is not closed twice
    _, errors = bank.apply([f1], [Action('SUPERSEDE', 1, 'M9')], _unit, now)
    assert errors == [('bad_target', 'M9')] and bank.units[-1].valid  # degrades to ADD, the fact is kept
    _, errors = bank.apply([], [Action('EXPIRE', old='M3')], _unit, now, shown=['M4'])
    assert errors == [('bad_target', 'M3')] and bank.by_id['M3'].valid  # never shown to the manager: not closed


# ---------------------------------------------------------------- r_ind
def test_worked_example_passes_rind():
    w = example_window()
    facts = example_facts(w)
    actions = [Action('ADD', i) for i in range(1, len(facts) + 1)]
    r = check_window(w, facts, actions, MemoryBank(), CAL)
    assert r.all_pass, r.violations
    for check in ('entity_grounded', 'time_tool', 'shift_resolved', 'shift_correct', 'one_action'):
        assert r.by_check[check][1] > 0


def test_relative_shift_rule_matches_gold():
    """Every EN turn with a gold resolved_shift: the rule's shift equals the gold one."""
    n = 0
    for sp in ('train', 'dev', 'test', 'dev_ext', 'test_ext'):
        for s in read_jsonl(ROOT / 'data/generated-EN' / sp / 'sessions.jsonl'):
            for t in s['turns']:
                if 'resolved_shift' in t:
                    turn = Turn(t['dialog_id'], s['session_id'], s['site_id'], t['speaker_id'], t['speaker_role'],
                                t['timestamp'], t['text'], t['turn_type'], t.get('tool_name'))
                    assert expected_shift(CAL, s['site_id'], None, [turn]) == t['resolved_shift'], t['dialog_id']
                    n += 1
    assert n >= 600


def _violations(facts, actions=None, bank=None, **kw):
    w = example_window()
    actions = actions if actions is not None else [Action('ADD', i) for i in range(1, len(facts) + 1)]
    r = check_window(w, facts, actions, bank or MemoryBank(), CAL, **kw)
    return {c for c, _ in r.violations}, r


def test_rind_flags_each_rule():
    w = example_window()
    base = example_facts(w)
    f = lambda **kw: [Fact(**({'content': base[1].content, 'entities': ['CNC-M2-915'], 'time': '2026-03-10 09:15',
                                'shift': '2026-03-10 Day shift', 'source': 'tool', 'turns': ['T00005', 'T00006']} | kw))]
    assert _violations(f())[0] == set()
    assert _violations(f(entities=['CNC-M2-916']))[0] == {'entity_grounded'}          # hallucinated id
    assert _violations(f(entities=['the spindle']))[0] == {'entity_format'}           # in the text, not an id
    assert _violations(f(entities=['the gearbox']))[0] == {'entity_format', 'entity_grounded'}
    assert _violations(f(time='2026-03-10 09:40'))[0] == {'time_not_future', 'time_tool'}
    assert _violations(f(time='2026-03-10 09:14'))[0] == {'time_tool'}               # not this record's time
    assert _violations(f(time='just now'))[0] == {'time_format'}
    assert _violations(f(shift='2026-03-09 Night shift'))[0] == {'shift_correct'}
    assert _violations(f(source='human'))[0] == {'source'}                             # tool turns only
    assert _violations(f(turns=[], bad_turns=[12]))[0] >= {'turns', 'source'}
    prev = lambda **kw: [Fact(**({'content': base[0].content, 'source': 'human', 'turns': ['T00001']} | kw))]
    assert _violations(prev())[0] == {'shift_resolved'}                                # "previous shift" unresolved
    assert _violations(prev(shift='2026-03-10 Day shift'))[0] == {'shift_correct'}     # resolved to this shift
    assert _violations(prev(shift='2026-03-09 Night shift', time='2026-03-10 09:00'))[0] == {'time_in_shift'}
    assert _violations(f(), extract_complete=False)[0] == {'complete'}


def test_rind_only_checks_filled_industrial_fields():
    bare = [Fact('The operator acknowledged the plan.', source='human', turns=['T00008'])]
    v, r = _violations(bare)
    assert not v and not {'entity_format', 'time_format', 'shift_correct', 'shift_resolved'} & set(r.by_check)


def test_rind_shift_across_a_shift_change():
    lines = ['[2026-03-10T15:20:00+08:00] P201 (quality_engineer, human): The rework of LOT202603-915 is re-inspected.',
             '[2026-03-10T15:40:00+08:00] P201 (quality_engineer, human): LOT202603-915 passed re-inspection.']
    turns = make_turns(lines)
    w = Window(0, 'S0001', EXAMPLE_SITE, tuple(turns), (), tuple(t.dialog_id for t in turns))
    fact = lambda shift, time=None: [Fact('LOT202603-915 rework passed re-inspection.', ['LOT202603-915'], time, shift,
                                          'human', ['T00001', 'T00002'])]
    check = lambda facts: {c for c, _ in check_window(w, facts, [Action('ADD', 1)], MemoryBank(), CAL).violations}
    assert check(fact('2026-03-10 Day shift')) == check(fact('2026-03-10 Swing shift')) == set()
    assert check(fact('2026-03-09 Night shift')) == {'shift_correct'}
    assert check(fact('2026-03-10 Day shift', '2026-03-10 15:40')) == {'shift_correct'}  # a stated time decides


def test_rind_checks_manager_actions():
    w = example_window()
    facts = example_facts(w)
    bank = MemoryBank()
    old = Fact('CNC-M2-915 spindle temperature was 80°C.', ['CNC-M2-915'])
    other = Fact('Constraint: WO_7777 stays open.', ['WO_7777'])
    bank.apply([old, other], [Action('ADD', 1), Action('ADD', 2)], lambda f: _unit(f, '2026-03-09T09:00:00+08:00'),
               None)
    n = len(facts)
    ok = [Action('SUPERSEDE', 2, 'M1')] + [Action('ADD', i) for i in range(1, n + 1) if i != 2]
    assert _violations(facts, ok, bank)[0] == set()
    assert _violations(facts, [Action('SUPERSEDE', 2, 'M2')] + ok[1:], bank)[0] == {'same_entity'}
    assert _violations(facts, [Action('SUPERSEDE', 2, 'M9')] + ok[1:], bank)[0] == {'target_valid'}
    assert _violations(facts, ok, bank, shown=['M2'])[0] == {'target_valid'}               # M1 was not listed
    assert _violations(facts, ok + [Action('EXPIRE', old='M2')], bank)[0] == {'same_entity'}  # WO_7777 not in window
    assert _violations(facts, ok + [Action('NOOP', 1)], bank)[0] == {'one_action'}
    assert _violations(facts, ok[1:], bank)[0] == {'one_action'}                               # fact 2 left out
    assert _violations(facts, ok + [Action('MERGE', 1)], bank)[0] == {'action_valid'}
    early = MemoryBank()
    early.apply([old], [Action('ADD', 1)], lambda f: _unit(f, '2026-03-10T09:30:00+08:00'), None)
    assert _violations(facts, ok, early)[0] == {'time_order'}                                 # older than the entry


# ---------------------------------------------------------------- ShopMem-Writer, offline
_WIN = re.compile(r'^#(\d+) \(shift: [^)]*\) \[.*?\] \S+ \((\w+), (\w+)(?::\w+)?\): (.*)$', re.M)
_IDS = re.compile(r'[A-Z][A-Z0-9]*(?:[-_][A-Z0-9]+)+')


class ScriptedWriterLLM(LLM):
    """Offline Writer stub. Extractor: one fact per turn naming an identifier. Manager: SUPERSEDE the
    latest listed entry sharing an identifier, else ADD. cut=True drops the end marker of every first
    extractor reply (the continuation then returns the rest). Planner: the question's identifiers."""

    def __init__(self, cut=False):
        super().__init__('scripted-writer')
        self.cut = cut

    def _call(self, messages, params):
        system, user = messages[0]['content'], messages[-1]['content']
        if 'fact extractor' in system:
            window = messages[1]['content'].split('Current window:\n')[-1]
            facts = []
            for i, role, kind, text in _WIN.findall(window):
                ids = list(dict.fromkeys(_IDS.findall(text)))
                if ids:
                    src = 'tool' if 'tool' in kind else 'agent' if role == 'industrial_agent' else 'human'
                    facts.append(json.dumps({'content': text[:200], 'entities': ids, 'source': src,
                                             'turns': [int(i)]}))
            if len(messages) > 2:     # continuation: the facts after the ones already given
                done = len(messages[2]['content'].splitlines())
                return '\n'.join(facts[done:] + [END_MARK]), 0, 0
            if self.cut and len(facts) > 1:
                return '\n'.join(facts[:1]) + '\n{"content": "cut', 0, 0
            return '\n'.join(facts + [END_MARK]), 0, 0
        if 'memory bank' in system:
            facts = re.findall(r'^\[F(\d+)\] \(.*?(?:entities: (.*?))?\) ', user, re.M)
            mem = re.findall(r'^\[(M\d+)\] \(.*?entities: (.*?)\) ', user, re.M)
            out, closed = [], set()
            for i, ents in facts:
                ents = set(ents.split(', ')) if ents else set()
                hit = [m for m, e in mem if ents & set(e.split(', ')) and m not in closed]
                closed.update(hit[-1:])
                out.append({'fact': int(i), 'action': 'SUPERSEDE', 'old': hit[-1]} if hit else
                           {'fact': int(i), 'action': 'ADD'})
            return '\n'.join(json.dumps(o) for o in out) + '\n' + END_MARK, 0, 0
        ids = _IDS.findall(user)
        return json.dumps({'semantic_queries': [user], 'keywords': ids, 'entities': ids, 'order': 'all',
                           'depth': {'semantic': 2, 'lexical': 2, 'symbolic': 3}}), 0, 0


def test_writer_pipeline_offline(dev30, tmp_path):
    histories, questions, gold = dev30
    h = histories[0]
    trace = tmp_path / 'trace.jsonl'
    m = METHODS['shopmem_writer'](ScriptedWriterLLM(), 'en', gold=gold, embed_base_url='fake',
                                  trace_path=str(trace))
    m.ingest(h)
    s = m.memory_stats()
    assert s['windows'] == len(session_windows(h)) and s['facts'] > 0 and s['op_errors'] == 0
    assert s['SUPERSEDE'] > 0 and s['closed'] > 0 and s['entries'] == len(m.bank.units) - s['closed']
    assert s['truncated_replies'] == 0 and 0 < s['rind_mean'] <= 1
    rows = read_jsonl(trace)
    assert len(rows) == s['windows'] and all('rind' in r and r['extract']['complete'] for r in rows)
    assert all(u.source_turns and set(u.source_turns) <= m.turn_by_id.keys() for u in m.bank.units)
    q = next(q for q in questions if q.history_id == h.history_id and q.category == 'state_tracking')
    r = m.retrieve(q.query())
    subject = re.search(r'[A-Z][A-Z0-9\-]+-\d{3}', q.question).group()
    assert subject in r.context and not r.meta['historical'] and 'no longer valid' not in r.context
    early = next(q for q in questions if q.history_id == h.history_id and 'earliest' in q.question)
    r2 = m.retrieve(early.query())
    assert r2.meta['historical'] and 'no longer valid' in r2.context
    m.config['closed_view'] = 'all'
    assert m.retrieve(q.query()).meta['historical']
    m.config['closed_view'] = 'rule'
    ask = lambda text: m.historical(SimpleNamespace(text=text))
    assert not ask('What is the latest state of WO_0081?') and ask('Which constraints were registered during WO_0081?')
    assert ask('What was the first measure on X6132-M3-081, and is it still applied?')


def test_writer_continues_cut_replies(dev30):
    histories, _, gold = dev30
    h = histories[0]
    full = METHODS['shopmem_writer'](ScriptedWriterLLM(), 'en', gold=gold, embed_base_url='fake')
    full.ingest(h)
    cut = METHODS['shopmem_writer'](ScriptedWriterLLM(cut=True), 'en', gold=gold, embed_base_url='fake')
    cut.ingest(h)
    s = cut.memory_stats()
    assert s['continuations'] > 0 and s['truncated_replies'] == 0
    assert s['facts'] == full.memory_stats()['facts']     # nothing lost to the cut


class LoopingLLM(LLM):
    """Repeats the same action line until the token limit, as Qwen3-8B did in the smoke run."""

    def __init__(self):
        super().__init__('loop')
        self.calls = 0

    def _call(self, messages, params):
        self.calls += 1
        return '{"fact": 1, "action": "ADD"}\n' + '{"action": "EXPIRE", "old": "M28"}\n' * 50, 0, 0


def test_writer_stops_repetition_loops():
    m = METHODS['shopmem_writer'](LoopingLLM(), 'en', max_continue=3)
    m._wcfg()
    m.stats = {'bad_lines': 0, 'continuations': 0, 'truncated_replies': 0}
    m._lock = __import__('threading').Lock()
    objs, complete, raws = m._generate([{'role': 'user', 'content': 'x'}])
    assert len(objs) == 2 and not complete
    assert m.llm.calls == 2 and m.stats['truncated_replies'] == 1   # the continuation added nothing: stop


class RunawayLLM(LLM):
    """A manager that keeps adding fact numbers past the window's facts, as the TOPR Writer did."""

    def __init__(self):
        super().__init__('runaway')
        self.calls = 0

    def _call(self, messages, params):
        self.calls += 1
        return ''.join('{"fact": %d, "action": "ADD"}\n' % i for i in range(1, 60)), 0, 0


def test_writer_cuts_manager_runaway():
    m = METHODS['shopmem_writer'](RunawayLLM(), 'en', max_continue=2)
    m._wcfg()
    m.stats = {'bad_lines': 0, 'continuations': 0, 'truncated_replies': 0, 'runaway_stops': 0}
    m._lock = __import__('threading').Lock()
    objs, complete, raws = m._generate([{'role': 'user', 'content': 'x'}], n_facts=2)
    assert [o['fact'] for o in objs] == [1, 2] and not complete
    assert m.llm.calls == 1 and m.stats['runaway_stops'] == 1      # not continued
    objs, complete, raws = m._generate([{'role': 'user', 'content': 'y'}])
    assert len(objs) == 59                                          # extractor replies are not cut


# ---------------------------------------------------------------- bank snapshots, teacher traces, r_evid / M-Fail
def test_bank_before_rebuilds_the_live_state(dev30):
    histories, _, gold = dev30
    m = METHODS['shopmem_writer'](ScriptedWriterLLM(), 'en', gold=gold, embed_base_url='fake')
    windows, known = m.setup(histories[0])
    windows = windows[:40]
    live = []
    for w in windows:
        live.append([(u.uid, u.valid) for u in m.bank.units])
        m.commit(w, m.propose(w, known[w.index], m.extract(w, known[w.index])))
    from memeval.memory_ops import Unit
    final = [Unit.from_dict(json.loads(json.dumps(u.to_dict()))) for u in m.bank.units]   # through a file round trip
    assert any(not u.valid for u in final)
    for w, state in zip(windows, live):
        assert [(u.uid, u.valid) for u in MemoryBank.before(final, w.index).units] == state
    assert all(u.valid for u in m.bank.units if u.closed_window is None) and m.bank.units[0].window == 0


def test_teacher_generate_offline(dev30, tmp_path):
    from memeval.teacher import generate_history
    histories, _, _ = dev30
    h = histories[0]
    generate_history(h, {'embed_base_url': 'fake'}, ScriptedWriterLLM(), 2, tmp_path, max_windows=12)
    rows = read_jsonl(tmp_path / 'samples' / f'{h.history_id}.jsonl')
    assert len(rows) == 12 * 3 and {r['sample'] for r in rows} == {0, 1, 2}
    assert all(isinstance(r['rind']['score'], float) and r['extract']['messages'] for r in rows)
    bank = read_jsonl(tmp_path / 'banks' / f'{h.history_id}.jsonl')
    assert bank and max(u['window'] for u in bank) == 11
    # a TOPR round: Writer samples 0..2 plus one teacher sample (k = 3) written by another model
    teacher = ScriptedWriterLLM()
    generate_history(h, {'embed_base_url': 'fake'}, ScriptedWriterLLM(), 2, tmp_path / 't', max_windows=4,
                     teacher=teacher, n_teacher=1)
    rows = read_jsonl(tmp_path / 't' / 'samples' / f'{h.history_id}.jsonl')
    assert len(rows) == 4 * 4 and {r['policy'] for r in rows if r['sample'] == 3} == {'teacher'}
    assert {r['policy'] for r in rows if r['sample'] < 3} == {'writer'}


def test_failure_breakdown():
    from memeval.data import Question
    from memeval.memory_ops import Unit
    from memeval.revid import failure_breakdown, r_evid, summarize
    t = datetime.fromisoformat('2026-01-01T08:00:00+08:00')
    unit = lambda turns: Unit('M1', 'x', [], t, None, 'human', turns, 'S1', t, t)
    q = lambda qid, ev: Question(qid, 'H', 'q?', 'single_hop_fact', True, 'x', 'ref', tuple(ev), (), {})
    qs = [q('a', ['T1']), q('b', ['T1', 'T2']), q('c', ['T1']), q('d', ['T1']), q('e', ['T9'])]
    preds = {x: {'provenance': ['T1']} for x in 'abcde'} | {'c': {'provenance': []}}
    scores = {'a': {'correct': True}, 'b': {'correct': False}, 'c': {'correct': False}, 'd': {'correct': False}}
    rows = failure_breakdown(qs, {'H': [unit(['T1'])]}, preds, scores)
    assert [r['kind'] for r in rows] == ['correct', 'memory', 'retrieval', 'reasoning']   # 'e' is unscored
    assert summarize(rows)['m_fail'] == 0.25
    assert r_evid(['T1', 'T2'], [unit(['T1'])]) == 0.5 and r_evid([], [unit(['T1'])]) is None


def test_sft_selection():
    from memeval.sft_data import chat_samples, select
    ok = lambda text: {'messages': [{'role': 'user', 'content': 'p'}], 'replies': [text], 'complete': True}
    fact = {'content': 'spindle vibration 3.5 mm/s', 'entities': [], 'time': None, 'shift': None, 'source': 'tool',
            'turns': ['T1'], 'bad_turns': []}

    def row(k, all_pass=True, extract='{"content": "x"}\n<END>', facts=1):
        return {'sample': k, 'rind': {'all_pass': all_pass, 'score': 1.0 if all_pass else 0.5},
                'extract': ok(extract), 'manage': ok('{"fact": 1, "action": "ADD"}\n<END>') if facts else
                {'messages': None, 'replies': [], 'complete': True},
                'facts': [fact] * facts, 'actions': [{'kind': 'ADD', 'fact': 1, 'old': None}] if facts else []}
    rows = [row(0), row(1, all_pass=False), row(2), row(3, extract='<think>a</think>\n{"c": 1}\nnot json\n<END>')]
    rw = {0: {'r_task': 0.4, 'r_evid': 1.0}, 1: {'r_task': 1.0, 'r_evid': 1.0}, 2: {'r_task': 0.8, 'r_evid': 0.5},
          3: {'r_task': 1.0, 'r_evid': 1.0}}
    kept, why = select('qa', rows, rw)
    assert kept['sample'] == 2 and why == 'best_reward'      # 1 fails r_ind, 3 has an unparsable line
    assert select('qa', rows, {0: rw[0]})[1] == 'pending'
    assert select('qa', rows, rw, min_r_task=0.9)[1] == 'below_min_r_task'
    assert select('noop', [row(0), row(1, facts=0)], {})[0]['sample'] == 1
    assert select('other', [row(0, all_pass=False), row(2)], {})[0]['sample'] == 2
    assert select('other', [row(0, all_pass=False)], {})[1] == 'no_candidate'
    s = chat_samples(row(0, extract='<think>x</think>{"content": "x"}\n<END>'), {'window': 0})
    assert [x['writer_role'] for x in s] == ['extractor', 'manager']
    assert s[0]['messages'][-1] == {'role': 'assistant', 'content': '{"content": "x"}\n<END>'}
    assert [x['writer_role'] for x in chat_samples(row(0, facts=0), {})] == ['extractor']
