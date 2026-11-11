"""r_ind: the rule-checked industrial reward of ShopMem-Writer (v2 方法论 §3.3.1).

Pure rules over what the Writer wrote for one window -- no LLM and no question labels, so it
can be computed on the factory floor. Required fields (content, source, source turns, one
action per fact, the end marker) are always checked; industrial fields (entities, time,
shift) only when filled, except that a source turn saying "the previous shift" / "this
shift" requires the shift to be resolved.

Checks (name: what passes)
  complete          each role's reply reached the end marker (was not cut off)
  content           the fact has text
  turns             the fact names source turns, all inside the window
  source            source is human / agent / tool and matches the kind of one of its source turns
  entity_format     an identifier has an M-LoCoMo ISA-95 format (memory_ops.ID_PATTERNS)
  entity_grounded   an identifier occurs verbatim in the window, the turns before it, or the
                    memory entries / known identifiers shown to the Writer (JSON fields included)
  time_format       time is absolute ("YYYY-MM-DD HH:MM" or a date)
  time_not_future   time is not after the last source turn
  time_tool         a tool-sourced fact is timed by a timestamp in its tool record (measured_at /
                    triggered_at); only a record without one lets the source turn's timestamp count
  time_in_shift     with a relative shift phrase in a source turn, time lies inside that shift
  shift_resolved    a relative shift phrase in a source turn is resolved to a shift ID
  shift_correct     the shift ID is the calendar shift of the phrase, else of time, else of one of
                    the source turns, named by its start date
  action_valid      the action is ADD / SUPERSEDE / EXPIRE / NOOP with its required arguments
  one_action        every fact gets exactly one action
  target_valid      SUPERSEDE / EXPIRE name a unit that was listed to the manager and is still valid,
                    closed only once
  same_entity       SUPERSEDE: the new fact and the old unit share an identifier (or one's identifier
                    is in the other's text); EXPIRE: an identifier of the old unit occurs in the window
  time_order        SUPERSEDE: the new fact's time is not before the old unit's valid_from
r_ind = passed / applicable. `all_pass` gates rejection sampling for SFT.

The relative-shift rule reproduces the dataset's gold resolved_shift on all 635 EN turns
that carry one (tests/test_writer.py).
"""
import re
from dataclasses import dataclass, field
from datetime import datetime

from .memory_ops import ACTIONS, SOURCES, entity_type, norm_text

TOOL_TYPES = {'agent_tool_call', 'tool_observation', 'tool_call'}
# relative shift phrases in M-LoCoMo turns; EN only (v2 experiments are English-only)
PREV_SHIFT = re.compile(r'previous shift|last shift|previous crew|shift handoff|shift change', re.I)
THIS_SHIFT = re.compile(r'this shift', re.I)
STAMP = re.compile(r'\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}')


def turn_kind(t):
    if t.turn_type in TOOL_TYPES:
        return 'tool'
    return 'agent' if t.speaker_role == 'industrial_agent' else 'human'


def parse_abs_time(value, tz=None):
    for fmt, n in (('%Y-%m-%d %H:%M', 16), ('%Y-%m-%dT%H:%M', 16), ('%Y-%m-%d', 10)):
        try:
            s = str(value).strip()
            if len(s) < n:
                continue
            return datetime.strptime(s[:n], fmt).replace(tzinfo=tz)
        except ValueError:
            continue
    return None


def shift_reference(turns):
    """(turn, previous?) of the first source turn with a relative shift phrase, else None."""
    for t in turns:
        if PREV_SHIFT.search(t.text):
            return t, True
        if THIS_SHIFT.search(t.text):
            return t, False
    return None


def expected_shift(calendar, site, fact_time, src_turns):
    ref = shift_reference(src_turns)
    if ref:
        t, prev = ref
        return calendar.label(site, datetime.fromisoformat(t.timestamp), previous=prev)
    anchor = fact_time or (datetime.fromisoformat(src_turns[0].timestamp) if src_turns else None)
    return calendar.label(site, anchor) if anchor else None


@dataclass
class RindReport:
    passed: int = 0
    total: int = 0
    violations: list = field(default_factory=list)   # (check, detail)
    by_check: dict = field(default_factory=dict)     # check -> [passed, total]

    def add(self, check, ok, detail=''):
        self.total += 1
        self.passed += bool(ok)
        c = self.by_check.setdefault(check, [0, 0])
        c[0] += bool(ok)
        c[1] += 1
        if not ok:
            self.violations.append((check, detail))

    @property
    def score(self):
        return self.passed / self.total if self.total else None

    @property
    def all_pass(self):
        return self.passed == self.total


def check_window(window, facts, actions, bank, calendar, extract_complete=True, manage_complete=True,
                 context_texts=(), shown=None):
    """r_ind report for one window. `bank` is the MemoryBank *before* this window's actions;
    `context_texts` are the texts shown to the Writer besides the window (memory entries,
    known identifiers) that an identifier may legitimately come from; `shown` are the unit ids
    listed to the manager, the only ones it may close (None: any)."""
    r = RindReport()
    turns = list(window.turns)
    by_id = {t.dialog_id: t for t in turns}
    tz = datetime.fromisoformat(turns[0].timestamp).tzinfo
    grounding = norm_text('\n'.join([t.text for t in turns] + [t.text for t in window.history] + list(context_texts)))
    r.add('complete', extract_complete, 'extractor')
    r.add('complete', manage_complete, 'manager')

    for i, f in enumerate(facts, 1):
        src = [by_id[x] for x in f.turns if x in by_id]
        r.add('content', bool(f.content.strip()), i)
        r.add('turns', bool(src) and not f.bad_turns, (i, f.bad_turns))
        r.add('source', f.source in SOURCES and any(turn_kind(t) == f.source for t in src), (i, f.source))
        for e in f.entities:
            r.add('entity_format', entity_type(e) is not None, (i, e))
            r.add('entity_grounded', norm_text(e) in grounding, (i, e))
        t = None
        if f.time:
            t = parse_abs_time(f.time, tz)
            r.add('time_format', t is not None, (i, f.time))
            if t and src:
                r.add('time_not_future', t <= max(datetime.fromisoformat(x.timestamp) for x in src), (i, f.time))
            if t and src and f.source == 'tool':
                stamps = {parse_abs_time(m.replace('T', ' '), tz) for x in src for m in STAMP.findall(x.text)}
                stamps = stamps or {datetime.fromisoformat(x.timestamp).replace(second=0) for x in src}
                r.add('time_tool', t in stamps, (i, f.time))
        ref = shift_reference(src)
        if ref:
            r.add('shift_resolved', bool(f.shift), i)
            if t:
                ref_label = calendar.label(window.site_id, datetime.fromisoformat(ref[0].timestamp), previous=ref[1])
                r.add('time_in_shift', calendar.label(window.site_id, t) == ref_label, (i, f.time, ref_label))
        if f.shift:
            want = expected_shift(calendar, window.site_id, t, src)
            ok = {norm_text(want)} if want else set()
            if not ref and not t:  # a fact resting on turns across a shift change may take any of their shifts
                ok |= {norm_text(calendar.label(window.site_id, datetime.fromisoformat(x.timestamp))) for x in src}
            r.add('shift_correct', norm_text(f.shift) in ok, (i, f.shift, want))

    counts, closed = {}, set()
    for a in actions:
        ok = a.kind in ACTIONS and (a.kind == 'EXPIRE' or (a.fact is not None and 1 <= a.fact <= len(facts))) \
            and (a.kind not in ('SUPERSEDE', 'EXPIRE') or bool(a.old))
        r.add('action_valid', ok, (a.kind, a.fact, a.old))
        if not ok:
            continue
        if a.fact is not None and a.kind != 'EXPIRE':
            counts[a.fact] = counts.get(a.fact, 0) + 1
        if a.kind not in ('SUPERSEDE', 'EXPIRE'):
            continue
        old = bank.by_id.get(a.old)
        r.add('target_valid', bool(old) and old.valid and a.old not in closed and (shown is None or a.old in shown),
              (a.kind, a.old))
        closed.add(a.old)
        if not old:
            continue
        old_ids = {norm_text(e) for e in old.entities}
        if a.kind == 'SUPERSEDE':
            f = facts[a.fact - 1]
            new_ids = {norm_text(e) for e in f.entities}
            if old_ids or new_ids:
                shared = bool(old_ids & new_ids) or any(x in norm_text(f.content) for x in old_ids) \
                    or any(x in norm_text(old.content) for x in new_ids)
                r.add('same_entity', shared, (a.fact, a.old))
            t = parse_abs_time(f.time, tz) if f.time else None
            if t:
                r.add('time_order', t >= old.valid_from, (a.fact, a.old))
        elif old_ids:
            window_ids = {norm_text(e) for f in facts for e in f.entities}
            text = norm_text('\n'.join(t.text for t in turns))
            r.add('same_entity', bool(old_ids & window_ids) or any(x in text for x in old_ids), ('EXPIRE', a.old))
    for i in range(1, len(facts) + 1):
        r.add('one_action', counts.get(i, 0) == 1, (i, counts.get(i, 0)))
    return r
