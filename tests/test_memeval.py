"""Tests for the memeval harness (run: .venv/bin/python -m pytest tests -q)."""
import json
import re
from dataclasses import fields
from pathlib import Path

import pytest

from memeval.data import ROOT, Turn, load, read_jsonl
from memeval.llm import DryRunLLM, count_tokens
from memeval.methods import METHODS
from memeval.methods.base import fit_recent
from memeval.metrics import bleu1, event_recall, f1, tokenize, turn_recall
from memeval.score import parse_label


@pytest.fixture(scope='module')
def dev():
    return load('zh', 'dev', 10)


def test_turns_expose_no_gold_labels():
    names = {f.name for f in fields(Turn)}
    assert not names & {'is_low_density', 'resolved_time', 'resolved_shift', 'resolved_entities', 'mentions',
                        'scenario_id'}


def test_ids_do_not_leak_scenario(dev):
    histories, _, _ = dev
    for t in histories[0].turns():
        assert re.fullmatch(r'T\d{5}', t.dialog_id) and re.fullmatch(r'S\d{4}', t.session_id)
        assert re.fullmatch(r'AGENT|SYS_(EMS|MES)|P\d{3}', t.speaker_id), t.speaker_id


def test_evidence_maps_back_to_original_ids(dev):
    _, questions, gold = dev
    raw = {q['question_id']: q for q in read_jsonl(ROOT / 'data/long/zh/dev/K010/questions.jsonl')}
    for q in questions[:50]:
        assert [gold.original_dialog_id[q.history_id][e] for e in q.evidence] == raw[q.question_id]['evidence']


def test_working_memory_reference_is_text(dev):
    _, questions, _ = dev
    wm = [q for q in questions if q.category == 'working_memory_snapshot']
    # v0.3: latest state + open item; constraints belong to constraint_recall
    assert wm and all(q.reference.startswith('最新状态：') and '待办' in q.reference and '约束' not in q.reference
                      for q in wm)


def test_fit_recent_keeps_newest_within_budget():
    blocks = [('=== S1 ===', [(f'old line {i}', f'T{i}') for i in range(50)]),
              ('=== S2 ===', [(f'new line {i}', f'U{i}') for i in range(5)])]
    text, ids, truncated = fit_recent(blocks, budget=40)
    assert truncated and all(f'U{i}' in ids for i in range(5)) and 'T0' not in ids
    assert count_tokens(text) <= 40 + count_tokens('=== S1 ===') + 2


def test_observation_mask_keeps_only_recent_observations(dev):
    histories, _, gold = dev
    m = METHODS['observation_mask'](DryRunLLM(), 'zh', gold=gold, keep_last=3, max_context_tokens=10**6)
    m.ingest(histories[0])
    text = m.retrieve(None).context
    assert text.count('的返回已省略]') > 0
    assert len(re.findall(r'返回：', text)) <= 3          # unmasked tool outputs
    assert 'agent_think' not in text


def test_oracle_gate_uses_gold_and_drops_low_density(dev):
    histories, _, gold = dev
    m = METHODS['oracle_gate'](DryRunLLM(), 'zh', gold=gold, max_context_tokens=10**6)
    m.ingest(histories[0])
    assert all(not gold.low_density[histories[0].history_id][t] for t in m.retrieve(None).provenance)
    plain = METHODS['full_context'](DryRunLLM(), 'zh', gold=gold)
    assert plain.gold is None  # non-oracle methods never see Gold


def test_metrics():
    assert f1('PRESS-LINE-07-003良率68.5%', 'PRESS-LINE-07-003良率68.5%', 'zh') == 1.0
    assert tokenize('良率68.5%', 'zh') == ['良', '率', '68.5%']
    assert f1('完全无关', '良率下降', 'zh') == 0.0
    assert 0 < bleu1('the yield dropped', 'yield dropped sharply', 'en') < 1
    assert turn_recall(['T1', 'T2'], ('T1', 'T3')) == 0.5 and turn_recall([], ()) is None
    assert event_recall(['T2'], ('E1', 'E2'), {'E1': ('T1', 'T2'), 'E2': ('T9',)}) == 0.5


def test_parse_label():
    assert parse_label('{"reason": "matches", "label": "CORRECT"}')
    assert not parse_label('{"reason": "not CORRECT here", "label": "WRONG"}')
    assert parse_label('reasoning... final: CORRECT')
    assert not parse_label('garbage')


def test_dry_run_pipeline(tmp_path):
    from memeval.run import main
    import sys
    argv = ['run', '--method', 'full_context', '--lang', 'en', '--pool', 'dev', '--K', '1', '--dry-run',
            '--limit-per-category', '1', '--max-histories', '2', '--out', str(tmp_path), '--run-name', 't']
    sys.argv = argv
    main()
    summary = json.loads((tmp_path / 't' / 'summary.json').read_text())
    preds = (tmp_path / 't' / 'predictions.jsonl').read_text().splitlines()
    assert summary['questions'] == len(preds) > 0
    assert summary['judge_acc_micro'] == 0.0  # the stub judges everything WRONG
    main()  # resume: nothing left to answer, same number of rows
    assert len((tmp_path / 't' / 'predictions.jsonl').read_text().splitlines()) == len(preds)


# --- retrieval baselines ---------------------------------------------------------------
from memeval.methods.rag import BM25, bm25_tokens, rrf


def test_bm25_tokens_keep_ids_whole():
    assert bm25_tokens('良率PRESS-LINE-07-003偏移') == ['良率', 'press-line-07-003', '偏移']
    assert bm25_tokens('Yield on LOT202604-088 dropped.') == ['yield', 'lot202604-088', 'dropped']
    assert '什么' not in bm25_tokens('Agent依据的证据是什么') and '证据' in bm25_tokens('Agent依据的证据是什么')


def test_bm25_prefers_exact_identifier():
    docs = ['PRESS-LINE-07-003 良率下降', 'PRESS-LINE-07-004 良率下降', '天气不错']
    s = BM25(docs).scores('PRESS-LINE-07-003 的良率')
    assert s.argmax() == 0 and s[2] == 0


def test_rrf_rewards_agreement():
    assert rrf([[1, 2, 3], [2, 1, 3]])[:2] in ([1, 2], [2, 1])
    assert rrf([[5, 1], [1, 7]])[0] == 1


@pytest.mark.parametrize('name', ['rag_bm25', 'rag_dense', 'rag_hybrid'])
def test_rag_retrieves_the_asked_entity(dev, name):
    histories, questions, gold = dev
    h = histories[0]
    m = METHODS[name](DryRunLLM(), 'zh', gold=gold, top_k=5, embed_base_url='fake')
    m.ingest(h)
    q = next(q for q in questions if q.history_id == h.history_id and q.category == 'tool_evidence_recall')
    r = m.retrieve(q.query())
    assert 0 < len(r.provenance) <= 5 and r.meta['hits'] == 5
    subject = re.search(r'[A-Z][A-Z0-9\-]+-\d{3}|LOT\d{6}-\d{3}', q.question).group()
    if name != 'rag_dense':  # the hashing stand-in embedder is too weak to guarantee this
        assert subject in r.context


def test_rag_chunks_and_oracle_gate(dev):
    histories, questions, gold = dev
    m = METHODS['oracle_gate_rag'](DryRunLLM(), 'zh', gold=gold, top_k=3, unit='chunk', chunk_turns=2,
                                   embed_base_url='fake')
    m.ingest(histories[0])
    r = m.retrieve(questions[0].query())
    assert all(not gold.low_density[histories[0].history_id][t] for t in r.provenance) and len(r.provenance) <= 6


# ---------------------------------------------------------------- ShopMem
from datetime import datetime  # noqa: E402

from memeval.llm import LLM  # noqa: E402
from memeval.methods.shopmem import ShiftCalendar, cliff_cut  # noqa: E402


class ScriptedLLM(LLM):
    """Offline ShopMem stub: one unit per window turn, no merging, a plan naming the question's IDs.
    garbage=True answers every write call with invalid JSON."""

    def __init__(self, garbage=False):
        super().__init__('scripted')
        self.garbage = garbage

    def _call(self, messages, params):
        system, user = messages[0]['content'], messages[-1]['content']
        if '"units"' in system:
            if self.garbage:
                return '{"units": [{"text": "cut off', 0, 0
            window = user.split('\n\n')[-1]
            turns = re.findall(r'^#(\d+) .*?\): (.*)$', window, re.M)
            units = [{'text': t, 'turns': [int(i)], 'entities': [{'id': x, 'type': 'Equipment'} for x in
                                                                  re.findall(r'[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+', t)],
                      'time_ref': None} for i, t in turns]
            return json.dumps({'units': units}, ensure_ascii=False), 0, 0
        if '"entries"' in system:
            n = len(re.findall(r'^\[\d+\]', user, re.M))
            return json.dumps({'entries': [{'from': [i], 'text': 'x'} for i in range(1, n + 1)]}), 0, 0
        ids = re.findall(r'[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+', user)
        return json.dumps({'semantic_query': user, 'keywords': ids, 'entities': ids, 'order': 'all',
                           'depth': {'semantic': 1, 'lexical': 2, 'symbolic': 3}}), 0, 0


def test_cliff_cut():
    assert cliff_cut([0.9, 0.88, 0.87, 0.5, 0.49], k_min=1) == (3, 3)
    assert cliff_cut([0.9, 0.3, 0.29, 0.28], k_min=3, search_from_k_min=False) == (3, 1)  # k_min guards it
    assert cliff_cut([0.9, 0.5, 0.49, 0.48, 0.2, 0.19], k_min=2) == (4, 4)  # the top-1 drop is skipped
    assert cliff_cut([0.9, 0.8], k_min=3) == (2, None)


def test_identifier_regex_ignores_cjk_boundaries():
    from memeval.methods.shopmem import _ID
    assert _ID.findall('X6132-M3-081这边报警了，批次LOT202612-084和WO_0081') == ['X6132-M3-081', 'LOT202612-084',
                                                                             'WO_0081']


def test_shift_calendar_names_shift_by_start_date():
    cal = ShiftCalendar(json.loads((ROOT / 'data/generated/production_calendar.json').read_text()))
    t = datetime.fromisoformat('2026-04-05T03:20:00+08:00')
    assert cal.label('SITE_04', t) == '2026-04-04 夜班'                     # 20:00-08:00 two-shift site
    assert cal.label('SITE_02', datetime.fromisoformat('2026-02-02T19:27:00+08:00')) == '2026-02-02 中班'
    assert cal.label('SITE_02', datetime.fromisoformat('2026-02-02T19:27:00+08:00'), previous=True) == '2026-02-02 早班'


def test_shopmem_pipeline_offline(dev):
    histories, questions, gold = dev
    h = histories[0]
    m = METHODS['shopmem'](ScriptedLLM(), 'zh', gold=gold, embed_base_url='fake')
    m.ingest(h)
    stats = m.memory_stats()
    assert stats['entries'] > 0 and stats['parse_failures'] == 0
    assert all(e.turns and e.shift for e in m.entries)
    turn_ids = {t.dialog_id for t in h.turns()}
    q = next(q for q in questions if q.history_id == h.history_id and q.category == 'state_tracking')
    r = m.retrieve(q.query())
    subject = re.search(r'[A-Z][A-Z0-9\-]+-\d{3}', q.question).group()
    assert subject in r.context and set(r.provenance) <= turn_ids
    assert r.meta['sem'][0]['k'] == 3 and r.meta['lex']['k'] == 5 and r.meta['sym']['k'] == 8  # per-view depths
    assert r.meta['sym']['matched'] > 0


def test_shopmem_never_drops_unparseable_windows(dev):
    histories, _, gold = dev
    h = histories[0]
    m = METHODS['shopmem'](ScriptedLLM(garbage=True), 'zh', gold=gold, embed_base_url='fake', synth=False)
    m.ingest(h)
    covered = {t for e in m.entries for t in e.turns}
    assert covered == {t.dialog_id for t in h.turns()}   # every turn survives as a verbatim fallback
    # every turn falls back verbatim at least once; turns in the 2-turn overlap of consecutive windows
    # (sessions longer than one window, common since the v0.5 call/observation split) fall back twice
    assert m.memory_stats()['verbatim_fallbacks'] >= len(h.turns())


def test_shopmem_without_compression_stores_windows_verbatim(dev):
    histories, questions, gold = dev
    h = histories[0]
    llm = ScriptedLLM()
    m = METHODS['shopmem'](llm, 'zh', gold=gold, embed_base_url='fake', compress=False)
    m.ingest(h)
    stats = m.memory_stats()
    assert stats['units'] == stats['windows'] == len(m.entries) and stats['merged_units'] == 0
    assert 'write' not in llm.meter.snapshot()   # no extraction or synthesis calls
    assert {t for e in m.entries for t in e.turns} == {t.dialog_id for t in h.turns()}
    assert all(e.shift and e.time == e.record_time for e in m.entries)
    q = next(q for q in questions if q.history_id == h.history_id)
    assert m.retrieve(q.query()).context


def test_parse_answer_and_example_modes():
    from memeval.prompts import parse_answer
    from memeval.methods.shopmem_prompts import render_example
    assert parse_answer('{"reasoning": "r", "answer": "2026-02-02 中班"}') == '2026-02-02 中班'
    assert parse_answer('plain text') == 'plain text'
    gated, kept = render_example('zh', 'llm', True), render_example('zh', 'keep_all', True)
    assert '61°C' not in gated.split('示例输出')[1] and '61°C' in kept.split('示例输出')[1]
    assert 'Equipment' in gated and 'Equipment' not in render_example('zh', 'llm', False)


def test_shopmem_multi_query_and_reflection(dev):
    histories, questions, gold = dev
    h = histories[0]
    m = METHODS['shopmem'](ScriptedLLM(), 'zh', gold=gold, embed_base_url='fake', reflect=True)
    m.ingest(h)
    q = next(q for q in questions if q.history_id == h.history_id)
    r = m.retrieve(q.query())
    assert isinstance(r.meta['sem'], list) and r.meta['semantic_queries'] == 1 and 'reflect' in r.meta


def test_shopmem_rerank_evidence_and_entity_groups(dev):
    histories, questions, gold = dev
    h = histories[0]
    m = METHODS['shopmem'](ScriptedLLM(), 'zh', gold=gold, embed_base_url='fake', rerank_base_url='fake')
    m.ingest(h)
    q = next(q for q in questions if q.history_id == h.history_id and q.category == 'state_tracking')
    r = m.retrieve(q.query())
    subject = re.search(r'[A-Z][A-Z0-9\-]+-\d{3}', q.question).group()
    assert r.meta['sem'][0]['pool'] >= r.meta['sem'][0]['kept'] and 'pool' in r.meta['lex']
    assert r.context.startswith(f'【实体 {subject}】')                 # the asked entity's group comes first
    assert r.context.count('该实体最新状态') == r.context.count('【实体')  # one latest mark per group
    # scripted units copy tool turns verbatim, so no evidence line duplicates them
    assert '证据原文' not in r.context


def test_rag_rerank(dev):
    histories, questions, gold = dev
    m = METHODS['rag_hybrid'](DryRunLLM(), 'zh', gold=gold, top_k=5, embed_base_url='fake', rerank_base_url='fake')
    m.ingest(histories[0])
    assert m.retrieve(questions[0].query()).meta['hits'] == 5


def test_batched_judge_falls_back_per_item():
    from memeval.score import parse_batch
    reply = '{"results": [{"id": 1, "reason": "ok", "label": "CORRECT"}, {"id": 3, "label": "maybe"}]}'
    got = parse_batch(reply, {1, 2, 3})
    assert set(got) == {1} and got[1][0] is True      # 2 missing and 3 malformed -> judged alone
    assert parse_batch('not json', {1}) == {}


def test_parse_answer_recovers_answer_from_broken_json():
    from memeval.prompts import parse_answer
    broken = '{\n  "reasoning": "在S0130记录中，质量工程师说"可以关闭"，但未提及。",\n  "answer": "历史记录中没有相关信息"\n}'
    assert parse_answer(broken) == '历史记录中没有相关信息'
    assert parse_answer('{"reasoning": "x", "answer": "含\\"引号\\"的答案"}') == '含"引号"的答案'


def test_gold_labels_per_history_with_several_histories():
    """Opaque ids restart per history; evidence turns are never low-density in any history."""
    histories, questions, gold = load('zh', 'eval', 30)
    assert len(histories) > 1
    for q in questions:
        assert not any(gold.low_density[q.history_id][e] for e in q.evidence), q.question_id



# ---------------------------------------------------------------- Responses API path (micu-gpt)
class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _responses_llm(tmp_path, output, api='responses'):
    from memeval.llm import OpenAICompatLLM, DiskCache
    llm = OpenAICompatLLM('gpt-6.1-sol', 'http://unused/v1', '', api=api, cache=DiskCache(tmp_path / 'c.sqlite'))
    calls = []

    def create(**kw):
        calls.append(kw)
        return _Obj(output=output, usage=_Obj(input_tokens=10, output_tokens=5))
    llm.client = _Obj(responses=_Obj(create=create))
    return llm, calls


def _msg(text, phase):
    return _Obj(type='message', phase=phase, content=[_Obj(type='output_text', text=text)])


def test_responses_keeps_first_message_only(tmp_path):
    search = '{"reasoning": "r", "action": "search", "query": "q", "include_closed": true}'
    answer = '{"reasoning": "r", "action": "answer", "answer": "a"}'
    llm, calls = _responses_llm(tmp_path, [_Obj(type='reasoning', content=[]), _msg(search, 'commentary'),
                                           _Obj(type='reasoning', content=[]), _msg(answer, 'final_answer')])
    msgs = [{'role': 'system', 'content': 'S'}, {'role': 'user', 'content': 'U'}]
    c = llm.complete(msgs, 'answer', seed=3, max_tokens=64)
    assert c.text == search
    assert calls[0]['instructions'] == 'S' and calls[0]['input'] == [{'role': 'user', 'content': 'U'}]
    assert 'seed' not in calls[0] and calls[0]['max_output_tokens'] == 64


def test_responses_and_chat_cache_keys_differ(tmp_path):
    from memeval.llm import OpenAICompatLLM, DiskCache
    llm, calls = _responses_llm(tmp_path, [_msg('from responses', 'final_answer')])
    msgs = [{'role': 'user', 'content': 'U'}]
    llm.complete(msgs, 'answer')
    chat = OpenAICompatLLM('gpt-6.1-sol', 'http://unused/v1', '', cache=DiskCache(tmp_path / 'c.sqlite'))
    chat.client = _Obj(chat=_Obj(completions=_Obj(create=lambda **kw: _Obj(
        choices=[_Obj(message=_Obj(content='from chat'))], usage=_Obj(prompt_tokens=1, completion_tokens=1)))))
    assert chat.complete(msgs, 'answer').text == 'from chat'  # not replayed from the Responses entry
    assert llm.complete(msgs, 'answer').cached and len(calls) == 1


def test_chat_cache_key_unchanged():
    import hashlib
    from memeval.llm import LLM
    llm = LLM('m')
    seen = {}

    class C:
        def get(self, k):
            seen['k'] = k
            return {'text': 't', 'prompt_tokens': 0, 'completion_tokens': 0}
    llm.cache = C()
    msgs = [{'role': 'user', 'content': 'U'}]
    llm.complete(msgs, 'answer', seed=1)
    params = {'temperature': 0.0, 'max_tokens': 1024, 'seed': 1}
    assert seen['k'] == hashlib.sha256(json.dumps(['m', msgs, params], ensure_ascii=False,
                                                  sort_keys=True).encode()).hexdigest()


# ---------------------------------------------------------------- reader_cold build: merging runs
def _traj(qid, sample, correct, searches=(), hinted=False, endpoint='yan-gpt'):
    reply = ('{"reasoning": "r", "action": "answer", "answer": "a"}')
    return {'question_id': qid, 'history_id': 'H', 'category': 'single_hop_fact', 'sample': sample,
            'hinted': hinted, 'correct': correct, 'judge_raw': None, 'teacher_endpoint': endpoint,
            'messages': [{'role': 'system', 'content': 'S'}, {'role': 'user', 'content': 'U'},
                         {'role': 'assistant', 'content': reply}],
            'prediction': 'a', 'searches': list(searches), 'format_ok': True, 'lenient_replies': 0}


def _write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(r) + '\n' for r in rows))


def test_merged_rows_takes_verdicts_from_judge_file(tmp_path):
    from memeval.reader_cold import merged_rows
    _write(tmp_path / 'a' / 'trajectories.jsonl', [_traj('Q1', 0, True)])
    _write(tmp_path / 'b' / 'trajectories.jsonl', [_traj('Q1', 1, None, endpoint='micu-gpt'),
                                                    _traj('Q1', 2, None, endpoint='micu-gpt')])
    _write(tmp_path / 'b' / 'judge_gpt-6-luna.jsonl', [{'question_id': 'Q1', 'sample': 1, 'correct': True},
                                                        {'question_id': 'Q1', 'sample': 2, 'correct': False}])
    _write(tmp_path / 'b' / 'judge_gpt-6-luna.quarantine.jsonl', [{'question_id': 'Q1', 'sample': 2, 'correct': True}])
    rows = {r['sample']: r for r in merged_rows(tmp_path, ['a', 'b'], 'gpt-6-luna')}
    assert rows[0]['correct'] is True and rows[0]['run'] == 'a'
    assert rows[1]['correct'] is True and rows[2]['correct'] is False  # quarantine file ignored


def test_merged_rows_rejects_missing_verdict_and_overlap(tmp_path):
    from memeval.reader_cold import merged_rows
    _write(tmp_path / 'b' / 'trajectories.jsonl', [_traj('Q1', 1, None)])
    with pytest.raises(ValueError, match='no inline verdict'):
        merged_rows(tmp_path, ['b'], 'gpt-6-luna')
    _write(tmp_path / 'a' / 'trajectories.jsonl', [_traj('Q1', 1, True)])
    _write(tmp_path / 'b' / 'judge_gpt-6-luna.jsonl', [{'question_id': 'Q1', 'sample': 1, 'correct': True}])
    with pytest.raises(ValueError, match='in both'):
        merged_rows(tmp_path, ['a', 'b'], 'gpt-6-luna')


def test_check_rows_rejects_saved_hint_and_bad_format_flag():
    from memeval.reader_cold import HINT, check_rows
    rows = [_traj('Q1', k, True) for k in range(2)]
    check_rows(rows, 2)
    with pytest.raises(ValueError, match='without 2 samples'):
        check_rows(rows[:1], 2)
    leaked = [_traj('Q1', 0, True), _traj('Q1', 1, True)]
    leaked[1]['messages'][0]['content'] += HINT
    with pytest.raises(ValueError, match='hint text'):
        check_rows(leaked, 2)
    glued = [_traj('Q1', 0, True), _traj('Q1', 1, True)]
    glued[1]['messages'][2]['content'] *= 2  # two JSON objects in one reply, still flagged format_ok
    with pytest.raises(ValueError, match='non-strict'):
        check_rows(glued, 2)


def test_merged_rows_applies_overrides(tmp_path):
    from memeval.reader_cold import merged_rows
    _write(tmp_path / 'a' / 'trajectories.jsonl', [_traj('Q1', 0, True), _traj('Q1', 1, False)])
    o = tmp_path / 'o.json'
    o.write_text(json.dumps([{'run': 'a', 'question_id': 'Q1', 'sample': 0, 'correct': False}]))
    rows = {r['sample']: r for r in merged_rows(tmp_path, ['a'], 'gpt-6-luna', str(o))}
    assert rows[0]['correct'] is False and rows[0]['verdict_override'] == {'from': True, 'to': False}
    assert rows[1]['correct'] is False and 'verdict_override' not in rows[1]
    o.write_text(json.dumps([{'run': 'a', 'question_id': 'Q9', 'sample': 0, 'correct': True}]))
    with pytest.raises(ValueError, match='not in the runs'):
        merged_rows(tmp_path, ['a'], 'gpt-6-luna', str(o))


def _vote(qid, pred, n_correct, raw_label=None):
    from memeval.reader_cold import pred_hash
    votes = [True] * n_correct + [False] * (5 - n_correct)
    raws = [json.dumps({'reason': 'r', 'label': raw_label or ('CORRECT' if v else 'WRONG')}) for v in votes]
    return {'question_id': qid, 'pred_hash': pred_hash(pred), 'votes': votes, 'n_correct': n_correct, 'raws': raws}


def test_apply_votes_threshold_and_hard_checks(tmp_path):
    from memeval.reader_cold import apply_votes
    rows = [_traj('Q1', 0, True) | {'run': 'a'}, _traj('Q2', 0, False) | {'run': 'a', 'prediction': 'b'}]
    v = tmp_path / 'v.jsonl'
    _write(v, [_vote('Q1', 'a', 3), _vote('Q2', 'b', 4)])
    apply_votes(rows, v, 4)
    assert rows[0]['correct'] is False and rows[0]['verdict_votes'] == {'from': True, 'to': False}
    assert rows[1]['correct'] is True and rows[1]['votes_correct'] == 4
    _write(v, [_vote('Q1', 'a', 5)])  # Q2 has no vote record
    with pytest.raises(ValueError, match='no vote record'):
        apply_votes(rows, v, 4)
    _write(v, [_vote('Q1', 'a', 5), _vote('Q2', 'b', 0, raw_label='UNSURE')])
    with pytest.raises(ValueError, match='unlabelled vote'):
        apply_votes(rows, v, 4)


def test_lapse_claim_only_flags_constraint_recall():
    from memeval.reader_cold import lapse_claim
    cr = {'category': 'constraint_recall'}
    assert lapse_claim(cr | {'prediction': 'WO_1 could not be closed ... This constraint stopped being valid on 2025-10-07.'})
    assert lapse_claim(cr | {'prediction': 'These constraints were valid until 2025-07-31 15:00, when verification passed.'})
    assert lapse_claim(cr | {'prediction': 'Both constraints were no longer valid after 09:46.'})
    assert not lapse_claim(cr | {'prediction': 'WO_1 could not be closed until verification was complete, and an '
                                                'adjustment that had already failed must not be reused.'})
    assert not lapse_claim({'category': 'superseded_fact', 'prediction': 'The old value was superseded on 05-01.'})
    # only the closure restriction lifted: true, kept; the record / both constraints / the reuse ban: lapse claim
    assert not lapse_claim(cr | {'prediction': 'Do not close WO_1 before verification. The closure restriction was '
                                                'lifted on 2026-05-08 after verification passed.'})
    assert not lapse_claim(cr | {'prediction': 'Verification passed, and WO_17 was cleared for closure at 13:07.'})
    assert lapse_claim(cr | {'prediction': 'The constraint record ceased to be valid when WO_1 was cleared for closure.'})
    assert lapse_claim(cr | {'prediction': 'The closure restriction was lifted. These constraints ceased to apply.'})


# ---------------------------------------------------------------- reader_rl: GRPO rewards and pool
def test_reader_rl_reward_gives_format_credit_only_when_correct():
    from memeval.reader_rl import reward
    assert reward(True, True, 100, 0.1) == (1.1, 1.0, 1.0, 0.0)
    assert reward(True, False, 100, 0.1) == (1.0, 1.0, 0.0, 0.0)
    assert reward(False, True, 100, 0.1) == (0.0, 0.0, 0.0, 0.0)


def test_reader_rl_length_penalty_ramps_then_caps():
    from memeval.reader_rl import reward
    assert reward(True, True, 512, 0.1)[3] == 0.0
    assert reward(True, True, 768, 0.1)[3] == -0.25
    assert reward(True, True, 5000, 0.1)[3] == -0.5
    assert reward(False, True, 5000, 0.1)[0] == -0.5  # a long wrong answer ranks below a short wrong one


def test_reader_rl_pool_keeps_only_mixed_groups():
    from memeval.reader_rl import pool_stats
    rows = [{'question_id': q, 'category': 'c', 'correct': ok, 'searches': [], 'format_ok': True}
            for q, oks in (('A', [1, 1]), ('B', [0, 0]), ('C', [1, 0]), ('D', [1])) for ok in oks]
    stats, mixed = pool_stats(rows, G=2)
    assert mixed == ['C']  # D is incomplete, A / B have equal rewards
    assert stats['questions_complete'] == 3 and stats['by_category']['c'] == {'all_correct': 1, 'all_wrong': 1, 'mixed': 1}
