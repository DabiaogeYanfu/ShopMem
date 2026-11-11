"""ShopMem-Writer (v2, 方法论_v2_大纲 §3.2): trainable memory writer, inference side.

Write stage, per session-bounded window (memeval.windows, v1's cut: 16 turns, overlap 2):
1. Extractor (one call per window, windows in parallel): the window (each turn with the shift
   it was sent in, from the site calendar -- as an MES stamps messages; v1 computed shifts by
   rule too), the session turns just before it, the site calendar and the site's recently
   seen identifiers -> candidate facts
   {content, entities, time, shift, source, turns} (memory_ops.Fact).
2. Manager (windows strictly in history order, since each sees the bank the previous ones
   left): the facts plus the related valid units -- units sharing an identifier (the most
   recent mgr_entity_k per fact), then the semantic top mgr_sem_k per fact -- -> one action
   per fact, ADD / SUPERSEDE / NOOP, plus EXPIREs (memory_ops.MemoryBank.apply).
Both roles are the same model with different prompts (shopmem_writer_prompts). Replies are
JSON Lines closed by an end marker; a reply cut off before it is continued (max_continue
rounds) and whatever parsed is kept, so a window is never dropped whole (unlike SimpleMem,
稿件/SimpleMem失效案例-断崖截断.md). Every window is scored by the r_ind rules (memeval.rind)
for diagnostics; with trace_path set, every window's prompts, replies, facts, actions and
r_ind report are appended as JSON Lines (teacher traces / SFT data).

Read stage: v1 ShopMem retrieval unchanged (multi-view, per-view depth, cliff cut, entity
groups), over the units visible at the question's as_of. By default only units still valid
then are searched; a question about an earlier state (keyword rule: first / initially /
previous / before / originally ..., else the v1 planner's order=earliest) searches closed
versions too, each marked with the time it stopped being valid. closed_view=all searches the
closed versions (so marked) for every question -- a constraint closed once its task closed still
answers "which constraints were registered". closed_view=rule: the historical keywords still search
closed versions; otherwise a question about the present (CURRENT: latest / current / now / still /
open ...) sees only the valid units and every other question sees the closed ones too.

Config (--method-config): writer_model / writer_base_url / writer_api_key_env ('' = local
vLLM) / writer_extra_body (JSON) -- without writer_base_url the answer LLM writes too;
actions ('all'; 'add_noop' = ablation w/o SUPERSEDE / EXPIRE: SUPERSEDE is applied as ADD and
EXPIRE is dropped, so no unit is ever closed), writer_temperature (0), max_write_tokens (2048), max_continue (2), mgr_entity_k (8),
mgr_sem_k (5), mgr_max_units (20), example (true), trace_path, write_workers (16);
window_size / overlap / history_turns / known_entities as v1; every v1 retrieval key
(views, depth, cliff, k_min, rerank_*, embed_*, ...).
request_units (false): after the write stage, every person's request turn (turn_type user_query) that
no unit cites as a source turn is stored as one more unit by rule, no LLM: "<role> (<speaker>) asked:
<text>", source "request", its time and shift. The extractor learned from the teacher to skip requests as
"plans without a result", so who asked for what -- e.g. who asked for the close-out confirmation, the
second half of every cross_role_dependency question -- was missing from the bank. Request units are
searched only for questions about roles (ROLE_QUESTION); every other question searches exactly the
bank without them, so its retrieval is unchanged.
"""
import copy
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import numpy as np

from ..data import DATA_DIRS, ROOT
from ..llm import OpenAICompatLLM, count_tokens
from ..memory_ops import Action, MemoryBank, entity_type, norm_text, parse_action, parse_fact, parse_lines
from ..prompts import render_calendar
from ..rind import check_window, parse_abs_time
from ..windows import session_windows
from .base import Retrieved, render_turn
from .shopmem import _COMPONENT, _ID, TOOL_TYPES, Entry, ShiftCalendar, ShopMem
from .shopmem_writer_prompts import CONTINUE, extraction_messages, management_messages, window_line

HISTORICAL = re.compile(r'\b(first|initial(ly)?|earliest|original(ly)?|previous(ly)?|before|prior|former(ly)?|'
                        r'used to|at the start|in the beginning)\b', re.I)
CURRENT = re.compile(r'\b(latest|last|current(ly)?|now|still|at (present|the moment)|open|unfinished|outstanding|'
                     r'pending|remain(s|ing)?)\b', re.I)
ROLE_QUESTION = re.compile(r'\broles?\b', re.I)  # questions that may search request units
_THINK = re.compile(r'<think>.*?</think>', re.S)


class _View(ShopMem):
    """v1 retrieval over one set of entries; planning is memoized by the Writer that owns it."""

    def _plan(self, query):
        return self.owner.plan(query)


def write_row(write, op_errors=None):
    """A proposed write as a JSON-able trace row."""
    r = write['rind']
    row = dict(write) | {'facts': [vars(f) for f in write['facts']], 'actions': [vars(a) for a in write['actions']],
                         'rind': {'score': r.score, 'all_pass': r.all_pass, 'by_check': r.by_check,
                                  'violations': [[c, str(d)] for c, d in r.violations]}}
    if op_errors is not None:
        row['op_errors'] = op_errors
    return row


class ShopMemWriter(ShopMem):
    name = 'shopmem_writer'
    thread_safe = True

    def _wcfg(self):
        self._cfg()
        c = self.config
        if self.lang != 'en':
            raise ValueError('shopmem_writer prompts are English-only (v2 runs on the EN data)')
        self.max_write_tokens = int(c.get('max_write_tokens', 2048))
        self.max_continue, self.temperature = int(c.get('max_continue', 2)), float(c.get('writer_temperature', 0.0))
        self.sample_temperature = float(c.get('sample_temperature', self.temperature))
        self.teacher, self.teacher_samples = None, set()  # set by memeval.teacher: samples another model writes
        self.mgr_entity_k, self.mgr_sem_k = int(c.get('mgr_entity_k', 8)), int(c.get('mgr_sem_k', 5))
        self.mgr_max_units = int(c.get('mgr_max_units', 20))
        self.writer = self.llm
        if c.get('writer_base_url'):
            extra = c.get('writer_extra_body')
            extra = json.loads(extra) if isinstance(extra, str) else extra
            self.writer = OpenAICompatLLM(c.get('writer_model', 'Qwen3-4B'), c['writer_base_url'],
                                          c.get('writer_api_key_env', ''), meter=self.llm.meter,
                                          cache=getattr(self.llm, 'cache', None))
            self.writer.default_params = {'extra_body': extra} if extra else {}

    # ---------------------------------------------------------------- write stage
    def ingest(self, history):
        windows, known = self.setup(history)
        with ThreadPoolExecutor(self.workers) as pool:
            extracted = list(pool.map(lambda w: self.extract(w, known[w.index]), windows))
        for w, ext in zip(windows, extracted):
            self.commit(w, self.propose(w, known[w.index], ext))
        if self.config.get('request_units'):
            self.add_request_units(windows)
        self.finish()

    def add_request_units(self, windows):
        """One rule-written unit per person's request turn no unit cites (see request_units)."""
        cited = {x for u in self.bank.units for x in u.source_turns}
        seen, n = set(), 0
        for w in windows:
            for t in w.turns:
                if t.turn_type != 'user_query' or t.dialog_id in cited or t.dialog_id in seen:
                    continue
                seen.add(t.dialog_id)
                when = datetime.fromisoformat(t.timestamp)
                role = t.speaker_role.replace('_', ' ')
                text = f'The {role} ({t.speaker_id}) asked the agent: {t.text}'
                ents = list(dict.fromkeys(_ID.findall(t.text) + _COMPONENT['en'].findall(t.text)))
                self.bank._new({'content': text, 'entities': ents, 'time': when,
                                'shift': self.calendar.label(w.site_id, when), 'source': 'request',
                                'source_turns': [t.dialog_id], 'session_id': w.session_id, 'record_time': when,
                                'valid_from': when}, window=w.index)
                n += 1
        self.stats['request_units'] = n

    def setup(self, history):
        """Config, calendar, empty bank and embedder for one history; returns (windows, known ids per window)."""
        self._wcfg()
        cal = json.loads((ROOT / 'data' / DATA_DIRS[self.lang] / 'production_calendar.json').read_text())
        self.calendar, self.calendar_text = ShiftCalendar(cal), render_calendar(cal, self.lang)
        self.history, self.turn_by_id = history, {t.dialog_id: t for t in history.turns()}
        self.stats = {'windows': 0, 'facts': 0, 'ADD': 0, 'SUPERSEDE': 0, 'EXPIRE': 0, 'NOOP': 0, 'op_errors': 0,
                      'continuations': 0, 'truncated_replies': 0, 'runaway_stops': 0, 'bad_lines': 0, 'rind_sum': 0.0, 'rind_windows': 0,
                      'rind_all_pass': 0}
        self._lock, self._plans = threading.Lock(), {}
        self.bank = MemoryBank()
        self._unit_vec = {}  # uid -> embedding of its content (manager's semantic lookup)
        from ..embed import make_embedder
        self.embedder = make_embedder(self.config.get('embed_base_url', 'http://localhost:8002/v1'),
                                      self.config.get('embed_model', 'Qwen3-Embedding-0.6B'),
                                      self.config.get('embed_cache', str(ROOT / '.cache' / 'embeddings.sqlite')))
        windows = session_windows(history, self.window_size, self.overlap, self.history_turns)
        self.stats['windows'] = len(windows)
        return windows, self._known_identifiers(history, windows)

    def finish(self):
        """Read-stage views; with bank_path, the bank (every version, with the windows that wrote and closed
        each unit) is written to <bank_path>/<history_id>.jsonl."""
        if self.config.get('bank_path'):
            from pathlib import Path
            out = Path(self.config['bank_path'])
            out.mkdir(parents=True, exist_ok=True)
            (out / f'{self.history.history_id}.jsonl').write_text(
                ''.join(json.dumps(u.to_dict(), ensure_ascii=False) + '\n' for u in self.bank.units))
        self._views = {}
        self._current_view = self._view(None, historical=False)

    def _known_identifiers(self, history, windows):
        """Per window: identifiers seen at the site before the window's session, most recent first (v1)."""
        recent, before_session, out = {}, {}, {}
        for s in history.sessions:
            before_session[s.session_id] = list(reversed(recent.get(s.site_id, {}).values()))[:self.n_known]
            site = recent.setdefault(s.site_id, {})
            for t in s.turns:
                for m in _ID.findall(t.text) + _COMPONENT[self.lang].findall(t.text):
                    key = m.split('#')[-1] if '#' in m else m
                    site.pop(key, None)
                    site[key] = m
        for w in windows:
            out[w.index] = before_session[w.session_id]
        return out

    def _count(self, key, n=1):
        with self._lock:
            self.stats[key] += n

    def _generate(self, messages, sample=None, n_facts=None):
        """JSON-Lines reply of the Writer, continued while it lacks the end marker. `sample` k > 0 draws
        the k-th independent sample at sample_temperature: it is sent as the request seed, so the disk
        cache keeps the samples apart; a k in teacher_samples is written by the teacher model at
        temperature 0 instead. A manager reply (`n_facts` given) is cut at its first action on a fact
        number outside 1..n_facts: a runaway "ADD fact 1..568" loop is not read or continued further.
        Returns (objects, complete, raw replies)."""
        objs, seen, raws, msgs = [], set(), [], list(messages)
        extra = {'seed': sample} if sample else {}
        llm, temperature = self.writer, self.sample_temperature if sample else self.temperature
        if sample in self.teacher_samples:
            llm, temperature = self.teacher, 0.0
        for attempt in range(self.max_continue + 1):
            text = llm.complete(msgs, stage='write', max_tokens=self.max_write_tokens,
                                temperature=temperature, **extra).text
            raws.append(text)
            got, complete, bad = parse_lines(_THINK.sub('', text))
            self._count('bad_lines', bad)
            fresh = []
            for o in got:  # a model stuck in a loop repeats lines: keep each object once
                key = json.dumps(o, sort_keys=True, ensure_ascii=False)
                if key not in seen:
                    seen.add(key)
                    fresh.append(o)
            if n_facts is not None:
                facts = [parse_action(o).fact for o in fresh]
                cut = next((i for i, f in enumerate(facts) if f is not None and not 1 <= f <= n_facts), None)
                if cut is not None:
                    self._count('runaway_stops')
                    objs += fresh[:cut]
                    break
            objs += fresh
            if complete:
                return objs, True, raws
            if not fresh:  # a continuation that adds nothing new: stop, keep what parsed
                break
            if attempt < self.max_continue:
                self._count('continuations')
                done = '\n'.join(json.dumps(o, ensure_ascii=False) for o in got)
                msgs = msgs + [{'role': 'assistant', 'content': done}, {'role': 'user', 'content': CONTINUE}]
        self._count('truncated_replies')
        return objs, False, raws

    def extract(self, w, known, sample=None):
        """Extractor step: (facts, complete, messages, raw replies). Independent of the bank."""
        window = '\n'.join(window_line(i, self.calendar.label(w.site_id, datetime.fromisoformat(t.timestamp))
                                       if self.use_shift else None, render_turn(t, self.lang))
                           for i, t in enumerate(w.turns, 1))
        hist = '\n'.join(render_turn(t, self.lang) for t in w.history)
        msgs = extraction_messages(w.site_id, self.calendar_text, known, hist, window, self.example)
        objs, complete, raws = self._generate(msgs, sample)
        facts = [f for f in (parse_fact(o, w.turns) for o in objs) if f]
        return facts, complete, msgs, raws

    def _related(self, facts):
        """Valid units the manager compares the facts with: shared identifiers first, then semantic top-k."""
        picked = {}
        for f in facts:
            for u in self.bank.by_entities(f.entities)[-self.mgr_entity_k:]:
                picked[u.uid] = u
        valid = self.bank.valid_units()
        if valid and facts and self.mgr_sem_k:
            q = self.embedder.embed([f.content for f in facts], query=True)
            m = np.stack([self._unit_vec[u.uid] for u in valid])
            sims = m @ q.T
            for j in range(len(facts)):
                for i in np.argsort(-sims[:, j])[:self.mgr_sem_k]:
                    picked.setdefault(valid[int(i)].uid, valid[int(i)])
        units = sorted(picked.values(), key=lambda u: u.time)
        return units[-self.mgr_max_units:]

    def propose(self, w, known, extracted, sample=None):
        """Manager step against the current bank, which it does not change; `extracted` is extract()'s
        result. Returns the window's write (facts, actions, prompts, replies) with its r_ind report."""
        facts, extract_complete, ext_msgs, ext_raws = extracted
        related = self._related(facts) if facts else []
        actions, manage_complete, mgr_msgs, mgr_raws = [], True, None, []
        if facts:
            mgr_msgs = management_messages(w.turns[0].timestamp, w.turns[-1].timestamp, facts, related)
            objs, manage_complete, mgr_raws = self._generate(mgr_msgs, sample, n_facts=len(facts))
            actions = [parse_action(o) for o in objs]
        shown = [u.uid for u in related]
        report = check_window(w, facts, actions, self.bank, self.calendar, extract_complete, manage_complete,
                              context_texts=list(known) + [u.content + ' ' + ' '.join(u.entities) for u in related],
                              shown=shown)
        return {'history_id': self.history.history_id, 'window': w.index, 'session_id': w.session_id,
                'sample': sample or 0, 'policy': 'teacher' if sample in self.teacher_samples else 'writer',
                'turns': w.turn_ids,
                'extract': {'messages': ext_msgs, 'replies': ext_raws, 'complete': extract_complete},
                'facts': facts, 'manage': {'messages': mgr_msgs, 'replies': mgr_raws, 'complete': manage_complete,
                                           'related': shown},
                'actions': actions, 'rind': report}

    def apply_write(self, bank, w, write):
        """Apply a proposed write to `bank` (not necessarily self.bank); returns (new units, applied, errors)."""
        n_before = len(bank.units)
        actions = write['actions']
        if self.config.get('actions', 'all') == 'add_noop':
            actions = [Action('ADD', a.fact) if a.kind == 'SUPERSEDE' else a for a in actions if a.kind != 'EXPIRE']
        applied, errors = bank.apply(write['facts'], actions, lambda f: self._unit_fields(w, f),
                                     datetime.fromisoformat(w.turns[-1].timestamp), shown=write['manage']['related'],
                                     window=w.index)
        return bank.units[n_before:], applied, errors

    def commit(self, w, write):
        """Apply a proposed write to the Writer's own bank, update statistics, append the trace row."""
        new, applied, errors = self.apply_write(self.bank, w, write)
        if new:
            for u, v in zip(new, self.embedder.embed([u.content for u in new])):
                self._unit_vec[u.uid] = v
        self.stats['facts'] += len(write['facts'])
        for a in applied:
            self.stats[a.kind] += 1
        self.stats['op_errors'] += len(errors)
        report = write['rind']
        if report.score is not None:
            self.stats['rind_sum'] += report.score
            self.stats['rind_windows'] += 1
            self.stats['rind_all_pass'] += report.all_pass
        if self.config.get('trace_path'):
            with open(self.config['trace_path'], 'a') as fh:
                fh.write(json.dumps(write_row(write, errors), ensure_ascii=False) + '\n')

    def _unit_fields(self, w, f):
        by_id = {t.dialog_id: t for t in w.turns}
        src = [by_id[x] for x in f.turns if x in by_id] or list(w.turns)
        record = datetime.fromisoformat(src[0].timestamp)
        t = parse_abs_time(f.time, record.tzinfo) if f.time else None
        if t is None or t > max(datetime.fromisoformat(x.timestamp) for x in src):  # unusable or future: record time
            t = record
        return {'content': f.content, 'entities': list(f.entities), 'time': t, 'shift': f.shift, 'source': f.source,
                'source_turns': [x.dialog_id for x in src], 'session_id': w.session_id, 'record_time': record,
                'valid_from': t}

    # ---------------------------------------------------------------- read stage
    def _entry(self, u, historical):
        text = u.content
        if historical and not u.valid:
            text += f' [no longer valid since {u.valid_to:%Y-%m-%d %H:%M}]'
        src = [self.turn_by_id[x] for x in u.source_turns if x in self.turn_by_id]
        sources = sorted({f'tool:{x.tool_name or x.speaker_id}' if x.turn_type in TOOL_TYPES else
                          'agent' if x.speaker_role == 'industrial_agent' else f'human:{x.speaker_role}' for x in src})
        return Entry(text, list(u.source_turns), [{'id': e, 'type': entity_type(e)} for e in u.entities], u.time,
                     u.record_time, u.shift if self.use_shift else None, sources, u.session_id, u.time != u.record_time)

    def wants_requests(self, question):
        """Whether a question searches the request units (request_units): only questions about roles."""
        return bool(ROLE_QUESTION.search(question))

    def _view(self, as_of, historical, requests=False):
        key = (as_of, historical, requests)
        with self._lock:
            if key in self._views:
                return self._views[key]
        cut = datetime.fromisoformat(as_of) if as_of else None
        units = [u for u in self.bank.units if (cut is None or u.record_time <= cut)
                 and (requests or u.source != 'request')]
        if not historical:
            units = [u for u in units if u.valid_to is None or (cut is not None and u.valid_to > cut)]
        v = _View(self.llm, self.lang, **self.config)
        v._cfg()
        v.owner, v.calendar, v.calendar_text, v.turn_by_id = self, self.calendar, self.calendar_text, self.turn_by_id
        v.entries = sorted((self._entry(u, historical) for u in units), key=lambda e: e.time)
        v._associate(self.history)
        v._index()
        with self._lock:
            return self._views.setdefault(key, v)

    def plan(self, query):
        """v1 planning, one call per question for both views (a copy each time: retrieve edits the plan)."""
        key = (query.text, query.as_of)
        with self._lock:
            p = self._plans.get(key)
        if p is None:
            p = ShopMem._plan(self, query)
            with self._lock:
                p = self._plans.setdefault(key, p)
        return copy.deepcopy(p)

    def historical(self, query):
        """Whether the question searches closed versions too (see closed_view in the module docstring)."""
        mode = self.config.get('closed_view', 'auto')
        if mode == 'all' or HISTORICAL.search(query.text):
            return True
        if mode == 'rule':
            return not CURRENT.search(query.text)
        return self.plan(query)[0].get('order') == 'earliest'

    def retrieve(self, query):
        historical = self.historical(query)
        r = self._view(query.as_of, historical, self.wants_requests(query.text)).retrieve(query)
        r.meta['historical'] = historical
        return r

    def memory_stats(self):
        s = dict(self.stats)
        n = s.pop('rind_windows')
        s['rind_mean'] = s.pop('rind_sum') / n if n else None
        s['rind_all_pass'] = s['rind_all_pass'] / n if n else None
        v = self._current_view
        return s | {'entries': len(v.entries), 'closed': sum(not u.valid for u in self.bank.units),
                    'tokens': sum(count_tokens(v._render(e)) for e in v.entries)}
