"""Memory records and operations of ShopMem-Writer (v2), shared by the Writer and the r_ind checker.

The extractor turns a window into candidate facts; the manager answers each fact with one
action against the memory bank:
  ADD(f)              store fact f as a new unit
  SUPERSEDE(f, old)   store f and close unit `old` (valid_to = f's valid_from; old stays as history)
  EXPIRE(old)         close unit `old` without a successor (e.g. a constraint whose work order closed)
  NOOP(f)             store nothing
Both roles answer in JSON Lines closed by END_MARK; a reply without it was cut off
(`parse_lines` reports complete=False) and the Writer asks the model to continue.

Industrial fields (entities, time, shift) may be empty; the checker only checks what is
filled, so the same format runs on LoCoMo / LongMemEval.
"""
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

END_MARK = '<END>'
ACTIONS = ('ADD', 'SUPERSEDE', 'EXPIRE', 'NOOP')
SOURCES = ('human', 'agent', 'tool')

# M-LoCoMo identifier formats by ISA-95 class, most specific first (an equipment pattern
# would also match a lot number). The Writer lists bare identifiers; the class is read off here.
ID_PATTERNS = [
    ('MaterialLot', re.compile(r'LOT\d{6}-\d{3}|SP-\d{4}')),       # production lot, solder paste lot
    ('WorkOrder', re.compile(r'WO_\d{4}')),
    ('Alarm', re.compile(r'ALARM_\d{3}')),
    ('AlarmCategory', re.compile(r'[A-Z]+(?:_[A-Z]+)+')),         # alarm-log category, e.g. SPINDLE_OVERTEMP
    ('StorageLocation', re.compile(r'[A-Z]-\d{2}-\d{2}')),        # WMS bin, e.g. A-03-06
    ('Equipment', re.compile(r'[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*-\d{3}')),
    ('Component', re.compile(r'[A-Za-z][A-Za-z -]*? ?#\d{3}')),  # "temperature sensor #081", "sub-lot tag #304"
]


def entity_type(identifier):
    s = str(identifier).strip()
    return next((t for t, p in ID_PATTERNS if p.fullmatch(s)), None)


def norm_text(s):
    return re.sub(r'\s+', '', str(s)).lower()


@dataclass
class Fact:
    content: str
    entities: list = field(default_factory=list)   # identifiers as written in the log
    time: Optional[str] = None                     # as generated: "YYYY-MM-DD HH:MM" or None
    shift: Optional[str] = None                    # "YYYY-MM-DD <shift name>", named by the shift's start date
    source: Optional[str] = None                   # human | agent | tool
    turns: list = field(default_factory=list)      # opaque ids of the window turns it rests on
    bad_turns: list = field(default_factory=list)  # turn references outside the window


@dataclass
class Action:
    kind: str
    fact: Optional[int] = None   # 1-based index into the window's facts
    old: Optional[str] = None    # unit id


@dataclass
class Unit:
    uid: str
    content: str
    entities: list
    time: datetime                 # event time: the fact's time if usable, else its first source turn
    shift: Optional[str]
    source: Optional[str]
    source_turns: list
    session_id: str
    record_time: datetime          # timestamp of the first source turn
    valid_from: datetime
    valid_to: Optional[datetime] = None
    closed_by: Optional[str] = None  # successor unit id, or 'EXPIRE'
    window: Optional[int] = None         # index of the window that wrote it
    closed_window: Optional[int] = None  # index of the window that closed it

    @property
    def valid(self):
        return self.valid_to is None

    _TIMES = ('time', 'record_time', 'valid_from', 'valid_to')

    def to_dict(self):
        return {k: (v.isoformat() if k in self._TIMES and v else v) for k, v in vars(self).items()}

    @classmethod
    def from_dict(cls, d):
        return cls(**{k: (datetime.fromisoformat(v) if k in cls._TIMES and v else v) for k, v in d.items()})


def parse_lines(text):
    """JSON objects of a JSON-Lines reply (a JSON array is accepted too), whether it reached
    END_MARK, and how many non-empty lines did not parse."""
    text = re.sub(r'```(?:json|jsonl)?', '', text or '')
    complete = END_MARK in text
    body = text.split(END_MARK, 1)[0].strip()
    try:
        whole = json.loads(body)
        if isinstance(whole, list):
            return [x for x in whole if isinstance(x, dict)], complete, 0
        if isinstance(whole, dict):
            return [whole], complete, 0
    except json.JSONDecodeError:
        pass
    objs, bad = [], 0
    for line in body.splitlines():
        line = line.strip().rstrip(',')
        if not line or line in ('[', ']'):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            bad += 1
            continue
        if isinstance(obj, dict):
            objs.append(obj)
        else:
            bad += 1
    return objs, complete, bad


def _opt_str(v):
    return str(v).strip() if v not in (None, '', 'null', 'None') and str(v).strip() else None


def parse_fact(obj, window_turns):
    """A Fact from one extractor line; `turns` are window-local #numbers (1-based)."""
    content = str(obj.get('content') or obj.get('text') or '').strip()
    if not content:
        return None
    ents = obj.get('entities') or []
    ents = [ents] if isinstance(ents, str) else ents
    ents = [str(e.get('id') if isinstance(e, dict) else e).strip() for e in ents if e]
    refs = obj.get('turns') or []
    refs = [refs] if not isinstance(refs, list) else refs
    turns, bad = [], []
    for r in refs:
        try:
            i = int(str(r).lstrip('#'))
        except ValueError:
            bad.append(r)
            continue
        (turns if 1 <= i <= len(window_turns) else bad).append(i)
    source = _opt_str(obj.get('source'))
    return Fact(content, list(dict.fromkeys(e for e in ents if e)), _opt_str(obj.get('time')), _opt_str(obj.get('shift')),
                source.lower() if source else None, [window_turns[i - 1].dialog_id for i in dict.fromkeys(turns)], bad)


def parse_action(obj):
    kind = str(obj.get('action', '')).strip().upper()
    fact = obj.get('fact')
    try:
        fact = int(str(fact).lstrip('F#')) if fact not in (None, '') else None
    except ValueError:
        fact = None
    return Action(kind, fact, _opt_str(obj.get('old')))


class MemoryBank:
    """Units in write order; closed units stay (valid_to set) for questions about earlier states."""

    def __init__(self):
        self.units, self.by_id = [], {}

    @classmethod
    def before(cls, units, window):
        """The bank as it stood when `window` began, rebuilt from a finished bank's units
        (window / closed_window stamps): units written earlier, closed only if closed earlier."""
        bank = cls()
        for u in units:
            if u.window is not None and u.window < window:
                c = Unit(**vars(u))
                if c.closed_window is None or c.closed_window >= window:
                    c.valid_to = c.closed_by = c.closed_window = None
                bank.units.append(c)
                bank.by_id[c.uid] = c
        return bank

    def valid_units(self):
        return [u for u in self.units if u.valid]

    def by_entities(self, identifiers):
        keys = {norm_text(x) for x in identifiers if x}
        return [u for u in self.units if u.valid and keys & {norm_text(e) for e in u.entities}]

    def _new(self, unit_kw, window=None):
        uid = f'M{len(self.units) + 1}'
        u = Unit(uid=uid, window=window, **unit_kw)
        self.units.append(u)
        self.by_id[uid] = u
        return u

    def apply(self, facts, actions, make_unit, now, shown=None, window=None):
        """Apply the manager's actions. `make_unit(fact)` gives the Unit fields of a fact; `now`
        closes EXPIREd units; `shown` (unit ids listed to the manager) bounds what SUPERSEDE /
        EXPIRE may close -- the manager cannot judge a unit it never saw. Illegal operations
        degrade safely and are reported: SUPERSEDE of a missing / closed / unlisted unit becomes
        ADD, a bad EXPIRE is ignored, and a fact left without an action is added (a fact is
        never lost). `window` stamps the units written and closed. Returns (applied actions, errors)."""
        applied, errors, done = [], [], set()
        shown = set(shown) if shown is not None else None
        closable = lambda uid: uid in self.by_id and self.by_id[uid].valid and (shown is None or uid in shown)
        for a in actions:
            if a.kind not in ACTIONS:
                errors.append(('unknown_action', a.kind))
                continue
            if a.kind == 'EXPIRE':
                if not closable(a.old):
                    errors.append(('bad_target', a.old))
                    continue
                u = self.by_id[a.old]
                u.valid_to, u.closed_by, u.closed_window = now, 'EXPIRE', window
                applied.append(a)
                continue
            if a.fact is None or not 1 <= a.fact <= len(facts) or a.fact in done:
                errors.append(('bad_fact', a.fact))
                continue
            done.add(a.fact)
            f = facts[a.fact - 1]
            if a.kind == 'NOOP':
                applied.append(a)
            elif a.kind == 'ADD':
                self._new(make_unit(f), window)
                applied.append(a)
            else:  # SUPERSEDE
                ok = closable(a.old)
                new = self._new(make_unit(f), window)
                if ok:
                    old = self.by_id[a.old]
                    old.valid_to, old.closed_by = max(new.valid_from, old.valid_from), new.uid
                    old.closed_window = window
                    applied.append(a)
                else:
                    errors.append(('bad_target', a.old))
                    applied.append(Action('ADD', a.fact))
        for i, f in enumerate(facts, 1):
            if i not in done:
                errors.append(('unassigned', i))
                self._new(make_unit(f), window)
                applied.append(Action('ADD', i))
        return applied, errors
