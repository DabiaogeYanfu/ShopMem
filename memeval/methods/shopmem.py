"""ShopMem: the proposed method (稿件 §3), SimpleMem adapted to industrial dialogue agents.

Write stage (all LLM calls stage='write'):
1. Semantic structured compression (§3.2). The history is cut into windows inside each
   session (window_size turns, overlap turns shared with the previous window). One
   extraction call per window does the implicit semantic gate G (eq. 3-3: an empty unit
   list drops the window) and the de-linearization Φ (eq. 3-4): Φ_split into minimal
   units, Φ_entity resolving references to full identifiers with an ISA-95 class, Φ_time
   marking relative times. The prompt sees the window, the preceding turns of the session
   (h_j) and the identifiers seen earlier at the same site ("known entities", extracted by
   regex from the raw log), since later sessions often name only a component
   ("温度传感器#081") and never the machine.
   Φ_time's production-calendar attribute is computed, not generated: from the unit's
   time_ref ("previous_shift" / "current_shift" / explicit time) and the site calendar,
   the shift the event happened in is named by its start date, like the answer prompt.
   If the model leaves time_ref empty but a source turn says "上一班" / "previous shift",
   the reference is resolved by rule (an 8B model misses it often).
   A reply that is not valid JSON is never dropped silently (unlike SimpleMem, see
   稿件/SimpleMem失效案例-断崖截断.md): the window is split in half and re-extracted, and a
   single turn that still fails is stored verbatim; failures are counted in memory_stats.
   The agent's own replies (turn_type agent_response) are its committed, tool-verified
   conclusions: one the model's reply leaves uncovered is added verbatim, marked verified
   (keep_conclusions, on with the LLM gate) -- the 8B gate tends to drop them as repeats of
   the operator's report, although they carry the confirmed latest state.
   The prompt carries one worked industrial example (SimpleMem's extraction prompt is
   few-shot too), rendered per ablation mode (shopmem_prompts.render_example).
2. Online semantic synthesis (§3.3, eq. 3-6). Per session, one call merges units about
   the same object / event (e.g. an operator report and the tool check that verified it)
   and dedupes window overlaps. Merged entries keep the union of source turns and of
   evidence kinds (human speaker role / agent / tool name). A merged entry is dated by an
   event time the dialogue stated, else by its latest evidence (when the state was
   confirmed). A unit the reply does not cover is kept as it was, so synthesis cannot
   lose a unit.
3. Multi-view index (eq. 3-5): Qwen3-Embedding vectors (semantic), BM25 over text +
   identifiers (lexical), and metadata -- time, shift, ISA-95 entities -- (symbolic).

Read stage (planning call stage='retrieve'), §3.4:
- The planner returns q_sem, q_lex, q_sym and a separate depth d in 1..5 per view (eq.
  3-8); k_view = phi(d) (eq. 3-9, phi = depth_k). As in SimpleMem's planner, q_sem is a list
  of 1-4 targeted sub-queries (one per step of a multi-step question); each gets its own
  top-k_sem and cliff cut. Identifiers found by regex in the question are added to q_sym's
  entities, so a malformed plan still filters by ID.
- Optional (reflect=true): one round of SimpleMem-style reflection -- the model judges
  whether the retrieved entries suffice and, if not, adds up to 3 semantic sub-queries.
- Semantic / lexical retrieval take the top k_view; the cliff cut keeps the top
  tau = argmax_i (s_i - s_{i+1}), clipped to [k_min, k_view] (eq. 3-13, 3-14). The argmax
  runs over i >= k_min: over all i the drop after the top hit nearly always wins, so the
  cut would degenerate to a fixed k_min (cliff_from_k_min=false restores the literal form).
- Symbolic retrieval filters by entity (case-insensitive, either identifier containing
  the other), ISA-95 class and date range; order earliest / latest takes the first / last
  k_sym by event time, 'all' takes the k_sym most similar to q_sem. No cliff (no scores).
- With rerank_base_url set, sem / lex candidates (rerank_pool x k per sub-query / view) are
  scored by a cross-encoder (Qwen3-Reranker) against the question and the cliff cut runs on
  those scores: one calibrated [0, 1] scale for both views, where raw cosine and BM25 scores
  differ in scale and spread.
- Symbolic retrieval also follows the entity association graph one hop (assoc): identifiers
  co-occurring in a session are linked, so a question about a machine reaches the entry
  that closes its work order under the lot number only. The latest state entry among the
  matches is always included (latest_pointer), whatever order the planner chose.
- Union by entry id (eq. 3-15). The context groups entries under the ISA-95 entity they are
  about (a queried entity, or one linked to it, first; group_by_entity); inside a group the
  registered constraints and open items come first, apart from the chronological state
  timeline whose latest entry is marked as the entity's latest state; each entry shows time,
  shift and sources, and the verbatim tool returns it was built from (evidence) -- the
  exact values that tool-evidence questions ask for.

The site shift calendars are read from production_calendar.json: that is plant
configuration, the same text every method's answer prompt already contains.

Ablation switches (--method-config), matching 实验计划 E4:
  gate=llm|keep_all|drop_tools  (N1a keep_all: no gating; N1b drop_tools: tool returns removed)
  entity_norm=true|false        (N2: open-domain names, no ISA-95 ids/classes, no known-entity list)
  shift=true|false              (N3: ISO timestamps only)
  compress=true|false           (w/o semantic compression: each window stored verbatim as one entry --
                                 no extraction, gate or synthesis; regex ids, shift and provenance kept)
  synth=true|false              (w/o online synthesis)
  synth_sources=true|false      (N4: synthesis without evidence sources)
  depth=per_view|shared|fixed   (N5 shared: one depth for all views; fixed: no planner, k = fixed_k)
  cliff=true|false, k_min=3     (N6; cliff_from_k_min=false: literal eq. 3-13)
  views=sem,lex,sym             (E4-C: any subset)
  example=true|false            (few-shot extraction example, borrowed from SimpleMem)
  multi_query=true|false, max_sem_queries=4, reflect=false, keep_conclusions=true
  rerank_base_url (off), rerank_model (Qwen3-Reranker-4B), rerank_pool=2
  evidence=true, max_evidence=3, group_by_entity=true, assoc=true, latest_pointer=true
Other config: window_size (16), overlap (2), history_turns (4), known_entities (15),
depth_k ([3, 5, 8, 12, 20]), fixed_k (10), write_workers (16), max_write_tokens (4096),
embed_base_url / embed_model / embed_cache as in rag.py.
"""
import json
from pathlib import Path
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np

from ..data import DATA_DIRS, ROOT
from ..embed import make_embedder
from ..rerank import make_reranker
from ..llm import count_tokens
from ..prompts import render_calendar
from .base import MemoryMethod, Retrieved, render_turn
from .rag import BM25
from .shopmem_prompts import (ISA95_TYPES, extraction_messages, parse_json, planning_messages,
                              reflection_messages, synthesis_messages)

TOOL_TYPES = {'agent_tool_call', 'tool_observation', 'tool_call'}  # tool_observation: v0.5 JSON replies
# ASCII boundaries, not \b: CJK characters are word characters, so \b would cut "X6132-M3-081这边" to "X6132-M3"
_ID = re.compile(r'(?<![A-Za-z0-9])[A-Z][A-Z0-9]*(?:[-_][A-Z0-9]+)+(?![A-Za-z0-9])')
# relative shift references, resolved against the site calendar when the model leaves time_ref empty
_PREV_SHIFT = {'zh': re.compile(r'上一班|上个班|上一个班|上班次'), 'en': re.compile(r'previous shift|last shift', re.I)}
_COMPONENT = {'zh': re.compile(r'[一-鿿]{2,5}#\d+'), 'en': re.compile(r'(?:[A-Za-z]+ )?[A-Za-z]+ #\d+')}
SOURCE_NAMES = {'zh': {'human': '人工', 'agent': 'Agent', 'tool': '工具'},
                'en': {'human': 'operator', 'agent': 'agent', 'tool': 'tool'}}
VERIFIED = {'zh': '经核实确认：', 'en': 'Verified: '}
EVIDENCE_LABEL = {'zh': '证据原文', 'en': 'evidence'}
LATEST_LABEL = {'zh': '该实体最新状态', 'en': 'latest state of this entity'}
GROUP_LABEL = {'zh': '实体', 'en': 'Entity'}
OTHER_GROUP = {'zh': '其他', 'en': 'Other'}
NOT_STATE = {'zh': ('约束', '待办'), 'en': ('Constraint', 'Open item')}  # not a state: never marked latest
STANDING_LABEL = {'zh': '登记的约束与待办：', 'en': 'Registered constraints and open items:'}
TIMELINE_LABEL = {'zh': '状态时间线：', 'en': 'State timeline:'}
PRIMARY_TYPES = ('Equipment', 'MaterialLot', 'WorkOrder', 'Product')  # preferred group keys
SHIFT_LABEL = {'zh': '班次', 'en': 'shift'}
SOURCE_LABEL = {'zh': '来源', 'en': 'source'}


@dataclass
class Entry:
    text: str
    turns: list                      # opaque source turn ids (provenance)
    entities: list                   # [{'id', 'type'}]
    time: datetime                   # event time (Φ_time), falls back to the record time
    record_time: datetime            # timestamp of the first source turn
    shift: str = None                # production shift of `time`, named by its start date
    sources: list = field(default_factory=list)  # evidence kinds: 'human:<role>' | 'agent' | 'tool:<name>'
    session_id: str = ''
    explicit_time: bool = False      # time comes from the dialogue ("上一班", "15:57"), not the record time


def _parse_time(value, tz):
    for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d'):
        try:
            return datetime.strptime(value.strip()[:19], fmt).replace(tzinfo=tz)
        except (ValueError, AttributeError):
            continue
    return None


class ShiftCalendar:
    """Names the production shift containing a time, by the date the shift starts."""

    def __init__(self, calendars):
        self.site_calendar, self.calendars = calendars['site_calendar'], calendars['calendars']

    def shift_start(self, site, t):
        best = None
        for day in (t.date() - timedelta(days=1), t.date()):
            for s in self.calendars[self.site_calendar[site]]:
                h, m = map(int, s['start'].split(':'))
                start = datetime(day.year, day.month, day.day, h, m, tzinfo=t.tzinfo)
                if start <= t < start + timedelta(hours=s['hours']) and (best is None or start > best[0]):
                    best = (start, s['shift'])
        return best

    def label(self, site, t, previous=False):
        found = self.shift_start(site, t)
        if found and previous:
            found = self.shift_start(site, found[0] - timedelta(minutes=1))
        return f'{found[0].date()} {found[1]}' if found else None


def cliff_cut(scores, k_min, search_from_k_min=True):
    """Number of top candidates kept: the position of the largest drop, clipped to [k_min, len].

    The drop after the top hit is usually the largest one (dense and BM25 alike), so with
    search_from_k_min the cliff is searched only among positions >= k_min; otherwise
    (eq. 3-13 literally) tau is almost always 1 and the clip returns k_min every time.
    """
    k = len(scores)
    if k <= k_min:
        return k, None
    start = k_min if search_from_k_min else 1
    gaps = [scores[i - 1] - scores[i] for i in range(start, k)]  # drop after position i (1-based)
    tau = start + int(np.argmax(gaps))
    return min(max(tau, k_min), k), tau


def _norm_id(s):
    return re.sub(r'\s+', '', str(s)).upper()


def _entity_match(a, b):
    a, b = _norm_id(a), _norm_id(b)
    return bool(a) and bool(b) and (a == b or (min(len(a), len(b)) >= 4 and (a in b or b in a)))


class ShopMem(MemoryMethod):
    name = 'shopmem'
    thread_safe = True  # retrieve() only reads the index; planning goes through the thread-safe LLM client

    # ---------------------------------------------------------------- config
    def _cfg(self):
        c = self.config
        flag = lambda k, d: str(c.get(k, d)).lower() in ('1', 'true', 'yes')
        self.gate = c.get('gate', 'llm')
        self.entity_norm, self.use_shift = flag('entity_norm', True), flag('shift', True)
        self.compress = flag('compress', True)
        self.synth, self.synth_sources = flag('synth', True) and self.compress, flag('synth_sources', True)
        self.depth_mode, self.cliff, self.k_min = c.get('depth', 'per_view'), flag('cliff', True), int(c.get('k_min', 3))
        self.cliff_from_k_min = flag('cliff_from_k_min', True)
        self.example, self.multi_query = flag('example', True), flag('multi_query', True)
        self.keep_conclusions = flag('keep_conclusions', self.gate == 'llm')
        self.max_sem_queries, self.reflect = int(c.get('max_sem_queries', 4)), flag('reflect', False)
        self.evidence, self.max_evidence = flag('evidence', True), int(c.get('max_evidence', 3))
        self.group_by_entity = flag('group_by_entity', True)
        self.assoc_on, self.latest_pointer = flag('assoc', True), flag('latest_pointer', True)
        self.rerank_pool = float(c.get('rerank_pool', 2.0))
        views = c.get('views', 'sem,lex,sym')
        self.views = set(views.split(',') if isinstance(views, str) else views)
        depth_k = c.get('depth_k', [3, 5, 8, 12, 20])
        self.depth_k = json.loads(depth_k) if isinstance(depth_k, str) else list(depth_k)
        self.fixed_k = int(c.get('fixed_k', 10))
        self.window_size, self.overlap = int(c.get('window_size', 16)), int(c.get('overlap', 2))
        self.history_turns, self.n_known = int(c.get('history_turns', 4)), int(c.get('known_entities', 15))
        self.workers, self.max_write_tokens = int(c.get('write_workers', 16)), int(c.get('max_write_tokens', 4096))

    # ---------------------------------------------------------------- write stage
    def ingest(self, history):
        self._cfg()
        cal = json.loads((ROOT / 'data' / DATA_DIRS[self.lang] / 'production_calendar.json').read_text())
        self.calendar = ShiftCalendar(cal)
        self.calendar_text = render_calendar(cal, self.lang)
        self.stats = {'windows': 0, 'gated_windows': 0, 'units': 0, 'parse_failures': 0, 'verbatim_fallbacks': 0,
                      'merged_units': 0, 'kept_conclusions': 0}
        self._stats_lock = threading.Lock()

        self.turn_by_id = {t.dialog_id: t for t in history.turns()}
        windows = self._windows(history)
        self.stats['windows'] = len(windows)
        with ThreadPoolExecutor(self.workers) as pool:
            unit_lists = list(pool.map(self._extract, windows))
        by_session = {}
        for w, units in zip(windows, unit_lists):
            self.stats['gated_windows'] += not units
            self.stats['units'] += len(units)
            by_session.setdefault(w['session'].session_id, []).extend(units)
        with ThreadPoolExecutor(self.workers) as pool:
            merged = list(pool.map(self._synthesize, by_session.values()))
        self.entries = sorted((e for es in merged for e in es), key=lambda e: e.time)
        self._associate(history)
        self._index()
        if self.config.get('bank_path'):  # entries as memory_ops.Unit dicts, for memeval.revid (M-Fail)
            out = Path(self.config['bank_path'])
            out.mkdir(parents=True, exist_ok=True)
            (out / f'{history.history_id}.jsonl').write_text(''.join(json.dumps(
                {'uid': f'E{i}', 'content': e.text, 'entities': [x['id'] for x in e.entities], 'time': e.time.isoformat(),
                 'shift': e.shift, 'source': ','.join(e.sources) or None, 'source_turns': list(e.turns),
                 'session_id': e.session_id, 'record_time': e.record_time.isoformat(), 'valid_from': e.time.isoformat()},
                ensure_ascii=False) + '\n' for i, e in enumerate(self.entries)))

    def _associate(self, history):
        """Entity association graph: identifiers that occur in the same session (one work order's
        context: its equipment, material lot, work order, alarm) are linked -- the ISA-95
        produces / usesMaterial / executed-on relations, read off co-occurrence. The final
        "LOT202611-083 复核通过, WO_0083 可以关闭" never names PRESS-LINE-07-083, yet answers a
        question about that machine."""
        self.assoc = {}
        for s in history.sessions:
            ids = {_norm_id(m) for t in s.turns for m in _ID.findall(t.text)}
            ids |= {_norm_id(x['id']) for e in self.entries if e.session_id == s.session_id
                    for x in e.entities if x.get('id') and _ID.fullmatch(str(x['id']))}
            for a in ids:
                self.assoc.setdefault(a, set()).update(ids - {a})

    def _linked(self, query_entities):
        if not self.assoc_on:
            return set()
        direct = {_norm_id(q) for q in query_entities}
        return {n for q in direct for key, ns in self.assoc.items() if _entity_match(key, q) for n in ns} - direct

    def _count(self, key, n=1):
        with self._stats_lock:
            self.stats[key] += n

    def _windows(self, history):
        """Session-bounded windows with their local history h_j and the site's known identifiers."""
        windows, recent = [], {}  # site -> {identifier key: surface}, insertion order = recency
        step = max(1, self.window_size - self.overlap)
        for s in history.sessions:
            turns = [t for t in s.turns if not (self.gate == 'drop_tools' and t.turn_type in TOOL_TYPES)]
            known = list(reversed(recent.get(s.site_id, {}).values()))[:self.n_known]
            for i in range(0, max(len(turns) - self.overlap, 1), step):
                chunk = turns[i:i + self.window_size]
                if chunk:
                    windows.append({'session': s, 'turns': chunk, 'known': known if self.entity_norm else [],
                                    'history': turns[max(0, i - self.history_turns):i]})
            site = recent.setdefault(s.site_id, {})
            for t in s.turns:
                for m in _ID.findall(t.text) + _COMPONENT[self.lang].findall(t.text):
                    key = m.split('#')[-1] if '#' in m else m
                    site.pop(key, None)
                    site[key] = m
        return windows

    def _extract(self, w):
        if not self.compress:  # the window itself is the memory entry (chunk storage)
            turns = w['turns']
            ids = dict.fromkeys(m for t in turns for m in _ID.findall(t.text))
            return [self._entry(w['session'], turns, '\n'.join(render_turn(t, self.lang) for t in turns),
                                [{'id': x, 'type': None} for x in ids], 'current_shift')]  # dated by its first turn
        return self._extract_turns(w, w['turns'])

    def _extract_turns(self, w, turns):
        s = w['session']
        window = '\n'.join(f'#{i} {render_turn(t, self.lang)}' for i, t in enumerate(turns, 1))
        hist = '\n'.join(render_turn(t, self.lang) for t in w['history'])
        msgs = extraction_messages(self.lang, 'keep_all' if self.gate == 'keep_all' else 'llm', self.entity_norm,
                                   s.site_id, self.calendar_text, w['known'], hist, window, self.example)
        reply = parse_json(self.llm.complete(msgs, stage='write', max_tokens=self.max_write_tokens,
                                             temperature=0.0).text)
        units = reply.get('units') if reply else None
        if not isinstance(units, list):
            self._count('parse_failures')
            if len(turns) > 1:  # never drop a window silently: split and retry
                half = len(turns) // 2
                return self._extract_turns(w, turns[:half]) + self._extract_turns(w, turns[half:])
            self._count('verbatim_fallbacks')
            return [self._entry(s, turns, render_turn(turns[0], self.lang), [], None)]
        out = []
        for u in units:
            if not isinstance(u, dict) or not str(u.get('text', '')).strip():
                continue
            idx = [i for i in u.get('turns', []) if isinstance(i, int) and 1 <= i <= len(turns)]
            src = [turns[i - 1] for i in idx] or turns  # unknown source: attribute to the whole window
            ents = [e for e in u.get('entities', []) if isinstance(e, dict) and e.get('id')]
            out.append(self._entry(s, src, str(u['text']).strip(), ents, u.get('time_ref')))
        if self.keep_conclusions:  # the agent's committed replies are never gated out
            covered = {t for e in out for t in e.turns}
            for t in turns:
                if t.turn_type == 'agent_response' and t.dialog_id not in covered:
                    self._count('kept_conclusions')
                    ids = _ID.findall(t.text)
                    out.append(self._entry(s, [t], VERIFIED[self.lang] + t.text,
                                           [{'id': x, 'type': None} for x in ids], None))
        return out

    def _entry(self, session, turns, text, entities, time_ref):
        record = datetime.fromisoformat(turns[0].timestamp)
        t, previous, explicit = record, False, False
        if not time_ref and any(_PREV_SHIFT[self.lang].search(x.text) for x in turns):
            time_ref = 'previous_shift'
        if time_ref == 'previous_shift':
            previous = explicit = True
        elif isinstance(time_ref, str) and time_ref not in ('current_shift', 'null'):
            parsed = _parse_time(time_ref, record.tzinfo)
            if parsed and parsed <= datetime.fromisoformat(turns[-1].timestamp):  # a future time is a misparse
                t, explicit = parsed, True
        shift = self.calendar.label(session.site_id, t, previous) if self.use_shift else None
        if previous and self.use_shift:  # anchor the event time inside the previous shift
            prev = self.calendar.shift_start(session.site_id, self.calendar.shift_start(session.site_id, t)[0]
                                             - timedelta(minutes=1))
            t = prev[0] if prev else t
        sources = sorted({f'tool:{x.tool_name or x.speaker_id}' if x.turn_type in TOOL_TYPES else
                          'agent' if x.speaker_role == 'industrial_agent' else f'human:{x.speaker_role}' for x in turns})
        return Entry(text, [x.dialog_id for x in turns], entities, t, record, shift, sources, session.session_id,
                     explicit)

    def _synthesize(self, units):
        if not self.synth or len(units) < 2:
            return units
        listing = '\n'.join(f'[{i}] ({self._source_text(u.sources)}) {u.text}' for i, u in enumerate(units, 1))
        reply = parse_json(self.llm.complete(synthesis_messages(self.lang, self.synth_sources, listing),
                                             stage='write', max_tokens=self.max_write_tokens, temperature=0.0).text)
        if not reply or not isinstance(reply.get('entries'), list):
            return units
        out, used = [], set()
        for e in reply['entries']:
            if not isinstance(e, dict) or not str(e.get('text', '')).strip():
                continue
            idx = [i for i in dict.fromkeys(e.get('from', []))
                   if isinstance(i, int) and 1 <= i <= len(units) and i not in used]
            if not idx:
                continue
            used.update(idx)
            members = [units[i - 1] for i in idx]
            if len(members) == 1:
                out.append(members[0])  # a singleton keeps its extracted text and metadata
                continue
            self._count('merged_units', len(members))
            # a merged entry is dated by an event time the dialogue stated; otherwise by its last
            # piece of evidence, when the state it describes (e.g. "verified") was established
            explicit = [m for m in members if m.explicit_time]
            anchor = min(explicit, key=lambda m: m.time) if explicit else max(members, key=lambda m: m.time)
            ents = list({_norm_id(x['id']): x for m in members for x in m.entities}.values())
            out.append(Entry(str(e['text']).strip(), sorted({t for m in members for t in m.turns}), ents, anchor.time,
                             min(m.record_time for m in members), anchor.shift,
                             sorted({x for m in members for x in m.sources}), anchor.session_id, bool(explicit)))
        out += [u for i, u in enumerate(units, 1) if i not in used]  # never lose a unit the reply skipped
        return out

    def _index(self):
        c = self.config
        self.docs = [e.text + ' ' + ' '.join(x['id'] for x in e.entities) for e in self.entries]
        self.bm25 = BM25(self.docs)
        self.embedder = make_embedder(c.get('embed_base_url', 'http://localhost:8002/v1'),
                                      c.get('embed_model', 'Qwen3-Embedding-0.6B'),
                                      c.get('embed_cache', str(ROOT / '.cache' / 'embeddings.sqlite')))
        self.vectors = (self.embedder.embed([e.text for e in self.entries]) if self.entries
                        else np.zeros((0, 1), dtype=np.float32))
        self.reranker = make_reranker(c.get('rerank_base_url'), c.get('rerank_model', 'Qwen3-Reranker-4B'),
                                      c.get('rerank_cache', str(ROOT / '.cache' / 'rerank.sqlite')))

    # ---------------------------------------------------------------- read stage
    def _plan(self, query):
        default = {'semantic_queries': [query.text], 'keywords': [query.text], 'entities': [], 'entity_types': [],
                   'time_from': None, 'time_to': None, 'order': 'all'}
        if self.depth_mode == 'fixed':
            return default | {'depth': None}, True
        reply = parse_json(self.llm.complete(planning_messages(self.lang, self.depth_mode == 'shared', query.text,
                                                               query.as_of), stage='retrieve', temperature=0.0).text)
        plan = default | {k: v for k, v in (reply or {}).items() if v not in (None, '', [])}
        plan.setdefault('depth', {'semantic': 3, 'lexical': 3, 'symbolic': 3})
        return plan, reply is not None

    def _k(self, plan, view):
        if plan.get('depth') is None:
            return self.fixed_k
        d = plan['depth'] if isinstance(plan['depth'], dict) else {}
        if self.depth_mode == 'shared':  # one depth for all views, whatever the planner wrote
            vals = [v for v in d.values() if isinstance(v, (int, float))]
            d = {v: round(sum(vals) / len(vals)) if vals else 3 for v in ('semantic', 'lexical', 'symbolic')}
        try:
            level = min(max(int(d.get(view, 3)), 1), len(self.depth_k))
        except (TypeError, ValueError):
            level = 3
        return self.depth_k[level - 1]

    def _ranked(self, order, scores, k):
        top = [int(i) for i in order[:k]]
        kept, tau = (cliff_cut([float(scores[i]) for i in top], self.k_min, self.cliff_from_k_min)
                     if self.cliff else (len(top), None))
        return top[:kept], {'k': k, 'kept': kept, 'tau': tau}

    def _reranked(self, question, candidates, k):
        """Cross-encoder scores for the candidates, best first, cut at the cliff (at most k kept)."""
        cands = list(dict.fromkeys(int(i) for i in candidates))
        if not cands:
            return [], {'k': k, 'pool': 0, 'kept': 0, 'tau': None}
        scores = self.reranker.score(question, [self._render(self.entries[i], evidence=False) for i in cands])
        order = sorted(range(len(cands)), key=lambda j: -scores[j])[:k]
        top = [cands[j] for j in order]
        kept, tau = (cliff_cut([scores[j] for j in order], self.k_min, self.cliff_from_k_min)
                     if self.cliff else (len(top), None))
        return top[:kept], {'k': k, 'pool': len(cands), 'kept': kept, 'tau': tau}

    def _pool(self, k):
        return max(k, int(round(k * self.rerank_pool)))

    def _symbolic(self, plan, sims, k):
        ents = [str(x) for x in plan.get('entities', []) if x]
        types = {str(x) for x in plan.get('entity_types', []) if str(x) in ISA95_TYPES} if self.entity_norm else set()
        lo = _parse_time(str(plan['time_from']), None) if plan.get('time_from') else None
        hi = _parse_time(str(plan['time_to']), None) if plan.get('time_to') else None
        if not (ents or types or lo or hi):
            return [], {'k': k, 'matched': 0}
        linked = self._linked(ents)
        hits, n_linked = [], 0
        for i, e in enumerate(self.entries):
            direct = any(_entity_match(q, x['id']) for q in ents for x in e.entities) \
                or any(_norm_id(q) in _norm_id(e.text) for q in ents)
            via = not direct and any(_norm_id(x['id']) in linked for x in e.entities if x.get('id'))
            if ents and not (direct or via):
                continue
            n_linked += via
            if types and not any(x.get('type') in types for x in e.entities):
                continue
            day = e.time.replace(tzinfo=None)
            if (lo and day < lo) or (hi and day >= hi + timedelta(days=1)):
                continue
            hits.append(i)
        order = plan.get('order', 'all')
        if order == 'earliest':
            picked = hits[:k]
        elif order == 'latest':
            picked = hits[-k:]
        else:
            picked = sorted(hits, key=lambda i: -sims[i])[:k]
        # current-state pointer: the latest state entry of the asked entity (and entities linked
        # to it) always comes along, whatever order the planner chose
        states = [i for i in hits if not any(w in self.entries[i].text for w in NOT_STATE[self.lang])]
        if ents and states and self.latest_pointer and states[-1] not in picked:
            picked = picked + [states[-1]]
        return picked, {'k': k, 'matched': len(hits), 'linked': n_linked}

    def retrieve(self, query):
        if not self.entries:
            return Retrieved('', [], {'entries': 0})
        plan, plan_ok = self._plan(query)
        q_ids = _ID.findall(query.text) + _COMPONENT[self.lang].findall(query.text)
        plan['entities'] = list(dict.fromkeys([str(x) for x in plan.get('entities', [])] + q_ids))
        q_sems = self._semantic_queries(plan, query.text)
        sim_rows = self.vectors @ self.embedder.embed(q_sems, query=True).T  # entries x queries
        sims = sim_rows.max(axis=1)
        picked, meta = set(), {'plan_ok': plan_ok, 'depth': plan.get('depth'), 'semantic_queries': len(q_sems)}
        if 'sem' in self.views:
            k_sem, meta['sem'] = self._k(plan, 'semantic'), []
            if self.reranker:  # sub-queries widen one candidate pool; the cross-encoder ranks it against q
                pool = [i for j in range(len(q_sems)) for i in np.argsort(-sim_rows[:, j])[:self._pool(k_sem)]]
                ids, m = self._reranked(query.text, pool, k_sem)
                picked.update(ids)
                meta['sem'].append(m)
            else:
                for j in range(len(q_sems)):  # each sub-query gets its own top-k_sem and cliff cut
                    col = sim_rows[:, j]
                    ids, m = self._ranked(np.argsort(-col), col, k_sem)
                    picked.update(ids)
                    meta['sem'].append(m)
        if 'lex' in self.views:
            kw = plan.get('keywords')
            q_lex = ' '.join(map(str, kw)) if isinstance(kw, list) else str(kw)
            s = self.bm25.scores(q_lex + ' ' + ' '.join(plan['entities']))
            order, k_lex = [i for i in np.argsort(-s) if s[i] > 0], self._k(plan, 'lexical')
            ids, meta['lex'] = (self._reranked(query.text, order[:self._pool(k_lex)], k_lex) if self.reranker
                                else self._ranked(order, s, k_lex))
            picked.update(ids)
        if 'sym' in self.views:
            ids, meta['sym'] = self._symbolic(plan, sims, self._k(plan, 'symbolic'))
            picked.update(ids)
        if self.reflect and 'sem' in self.views:
            meta['reflect'] = self._reflect(query, picked, self._k(plan, 'semantic'))
        hits = sorted(picked)  # entries are stored chronologically
        meta['entries'] = len(hits)
        return Retrieved(self._context(hits, plan['entities']), sorted({t for i in hits for t in self.entries[i].turns}),
                         meta)

    def _semantic_queries(self, plan, question):
        """Planner sub-queries (SimpleMem-style targeted queries), capped at max_sem_queries; the
        question itself is always one of them."""
        qs = plan.get('semantic_queries', plan.get('semantic_query'))
        qs = [qs] if isinstance(qs, str) else qs if isinstance(qs, list) else []
        qs = [str(q).strip() for q in qs if str(q).strip()][:self.max_sem_queries]
        return list(dict.fromkeys(qs or [question])) if self.multi_query else [qs[0] if qs else question]

    def _reflect(self, query, picked, k_sem):
        """One round of sufficiency reflection: follow-up semantic queries if the model finds gaps."""
        reply = parse_json(self.llm.complete(reflection_messages(self.lang, query.text, self._context(sorted(picked))),
                                             stage='retrieve', temperature=0.0).text) or {}
        extra = [str(q) for q in reply.get('queries', []) if str(q).strip()][:3] \
            if reply.get('sufficient') is False else []
        before = len(picked)
        if extra:
            rows = self.vectors @ self.embedder.embed(extra, query=True).T
            for j in range(len(extra)):
                picked.update(self._ranked(np.argsort(-rows[:, j]), rows[:, j], k_sem)[0])
        return {'queries': len(extra), 'added': len(picked) - before}

    def _context(self, hits, query_entities=()):
        """Entries in chronological order; with group_by_entity, grouped under the ISA-95 entity
        they are about (a queried entity first), each group marking its latest state entry."""
        if not self.group_by_entity:
            return '\n'.join(self._render(self.entries[i]) for i in hits)
        groups = {}
        for i in hits:
            groups.setdefault(self._group_key(self.entries[i], query_entities), []).append(i)
        asked = lambda key: any(_entity_match(key, q) for q in query_entities)
        lines = []
        for key in sorted(groups, key=lambda g: (not asked(g), self.entries[groups[g][0]].time)):
            members = groups[key]
            standing = [i for i in members if any(w in self.entries[i].text for w in NOT_STATE[self.lang])]
            states = [i for i in members if i not in standing]
            lines.append(f'【{GROUP_LABEL[self.lang]} {key}】')
            if standing:  # constraints and open items stand apart from the state timeline they outlive
                lines.append(STANDING_LABEL[self.lang])
                lines += [self._render(self.entries[i]) for i in standing]
                lines.append(TIMELINE_LABEL[self.lang])
            lines += [self._render(self.entries[i], latest=(i == states[-1])) for i in states]
        return '\n'.join(lines)

    def _group_key(self, e, query_entities):
        ids = [x['id'] for x in e.entities if x.get('id')]
        for q in query_entities:  # an entry about the asked entity, or an entity linked to it
            if any(_entity_match(x, q) for x in ids) or {_norm_id(x) for x in ids} & self._linked([q]):
                return q
        primary = [x['id'] for x in e.entities if x.get('type') in PRIMARY_TYPES]
        return (primary or ids or [OTHER_GROUP[self.lang]])[0]

    def _source_text(self, sources):
        names = SOURCE_NAMES[self.lang]
        return '+'.join(f"{names[kind]}({detail})" if detail else names[kind]
                        for kind, _, detail in (s.partition(':') for s in sources))

    def _render(self, e, evidence=None, latest=False):
        head = [e.time.strftime('%Y-%m-%d %H:%M')]
        if e.shift:
            head.append(f'{SHIFT_LABEL[self.lang]}: {e.shift}')
        head.append(f'{SOURCE_LABEL[self.lang]}: {self._source_text(e.sources)}')
        if latest:
            head.append(LATEST_LABEL[self.lang])
        out = f"[{' | '.join(head)}] {e.text}"
        if self.evidence if evidence is None else evidence:
            out += ''.join(f'\n    {EVIDENCE_LABEL[self.lang]}: {t}' for t in self._evidence(e))
        return out

    def _evidence(self, e):
        """Verbatim text of the tool returns behind an entry (the gate already dropped routine ones),
        unless the entry already quotes it."""
        norm = lambda x: re.sub(r'\s+', '', x)
        out = []
        for tid in e.turns:
            t = self.turn_by_id.get(tid)
            if t and t.turn_type in TOOL_TYPES and norm(t.text) not in norm(e.text):
                out.append(t.text)
        return out[:self.max_evidence]

    def memory_stats(self):
        return self.stats | {'entries': len(self.entries),
                             'tokens': sum(count_tokens(self._render(e)) for e in self.entries)}
