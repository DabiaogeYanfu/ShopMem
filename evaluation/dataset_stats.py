"""Corpus and long-history statistics for the paper's dataset tables.

Tokens use tiktoken o200k_base (GPT-4o / GPT-4.1 family), the same unit SimpleMem
reports as Token Cost. "text" tokens count turn text only; "formatted" tokens count
each turn rendered as a full-context prompt line: "[timestamp] role: text".

Writes data/long/stats.json and data/long/STATS.md.
"""
import json
import statistics as st
from collections import Counter, defaultdict
from pathlib import Path

import tiktoken

ROOT = Path(__file__).resolve().parents[1] / 'data'
LANGS = {'zh': 'generated', 'en': 'generated-EN'}
ENC = tiktoken.get_encoding('o200k_base')
TURN_TYPES = ['user_query', 'agent_think', 'agent_tool_call', 'tool_observation', 'agent_response', 'human', 'tool_call']
_cache = {}


def ntok(s):
    if s not in _cache:
        _cache[s] = len(ENC.encode(s))
    return _cache[s]


def fmt_turn(t):
    return f"[{t['timestamp']}] {t['speaker_role']}: {t['text']}"


def read_jsonl(path):
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def corpus_stats(lang):
    d = ROOT / LANGS[lang]
    scen, sess, qs = [], [], []
    for sp in ['train', 'dev', 'test']:
        scen += read_jsonl(d / sp / 'scenarios.jsonl')
        sess += read_jsonl(d / sp / 'sessions.jsonl')
        qs += read_jsonl(d / sp / 'questions.jsonl')
    turns = [t for s in sess for t in s['turns']]
    tok_by_scen = defaultdict(int)
    for s in sess:
        tok_by_scen[s['scenario_id']] += sum(ntok(fmt_turn(t)) for t in s['turns'])
    by_type = {}
    for tt in TURN_TYPES:
        ts = [t for t in turns if t.get('turn_type') == tt]
        toks = sum(ntok(t['text']) for t in ts)
        by_type[tt] = {'turns': len(ts), 'tokens': toks,
                       'low_density_ratio': sum(t['is_low_density'] for t in ts) / len(ts) if ts else 0,
                       'tokens_per_turn': toks / len(ts) if ts else 0}
    total_text = sum(v['tokens'] for v in by_type.values())
    for v in by_type.values():
        v['token_share'] = v['tokens'] / total_text
    low_tok = sum(ntok(t['text']) for t in turns if t['is_low_density'])
    cats = defaultdict(list)
    for q in qs:
        cats[q['category']].append(q)
    calendars = json.loads((d / 'production_calendar.json').read_text())
    cal_of = {s['scenario_id']: s['production_calendar_id'] for s in scen}

    def shift_name(s):
        """Name of the calendar shift a session starts in."""
        t = s['session_start'][11:16]
        shifts = sorted(calendars['calendars'][cal_of[s['scenario_id']]], key=lambda x: x['start'])
        # the shift with the latest start at or before t; before the first start -> the last (overnight) shift
        return next((x['shift'] for x in reversed(shifts) if x['start'] <= t), shifts[-1]['shift'])

    by_cat = {c: {'questions': len(v),
                  'difficulty': dict(Counter(q['difficulty'] for q in v)),
                  'evidence_turns': st.mean(len(q['evidence']) for q in v),
                  'required_events': st.mean(len(q['required_events']) for q in v),
                  'answer_tokens': st.mean(ntok(json.dumps(q['answer'], ensure_ascii=False)
                                                if q['answer']['type'] != 'text' else q['answer']['reference'])
                                           for q in v)}
              for c, v in sorted(cats.items())}
    return {
        'scenarios': len(scen), 'domains': dict(Counter(s['domain'] for s in scen)),
        'sessions': len(sess), 'turns': len(turns), 'turns_per_session': len(turns) / len(sess),
        'turns_per_scenario': len(turns) / len(scen),
        'text_tokens': total_text, 'formatted_tokens_per_scenario': st.mean(tok_by_scen.values()),
        'questions': len(qs), 'unanswerable_questions': sum(not q.get('answerable', True) for q in qs),
        'evidence_per_question': st.mean(len(q['evidence']) for q in qs if q['evidence']),
        'low_density_turn_ratio': sum(t['is_low_density'] for t in turns) / len(turns),
        'low_density_token_ratio': low_tok / total_text,
        'turns_with_resolved_time': sum('resolved_time' in t for t in turns),
        'turns_with_resolved_shift': sum('resolved_shift' in t for t in turns),
        'turns_with_resolved_entities': sum('resolved_entities' in t for t in turns),
        'sessions_by_shift': dict(Counter(shift_name(s) for s in sess)),
        'by_turn_type': by_type, 'by_category': by_cat,
    }


def history_stats(path_dir):
    hists = read_jsonl(path_dir / 'histories.jsonl')
    qs = read_jsonl(path_dir / 'questions.jsonl')
    scen_domain = {}
    for lang_dir in LANGS.values():
        for sp in ['train', 'dev', 'test', 'dev_ext', 'test_ext']:  # pools dev30/test90 use the extension splits
            for s in read_jsonl(ROOT / lang_dir / sp / 'scenarios.jsonl'):
                scen_domain[s['scenario_id']] = s['domain']
    per_hist, depth_first, depth_last, distractors = [], [], [], []
    for h in hists:
        turns = [t for s in h['sessions'] for t in s['turns']]
        toks = [ntok(fmt_turn(t)) for t in turns]
        total = sum(toks)
        # suffix[i] = tokens from turn i to the end of the history.
        suffix, acc = [0] * len(toks), 0
        for i in range(len(toks) - 1, -1, -1):
            acc += toks[i]
            suffix[i] = acc
        pos = {t['dialog_id']: i for i, t in enumerate(turns)}
        low = sum(n for n, t in zip(toks, turns) if t['is_low_density'])
        starts = Counter(s['session_start'] for s in h['sessions'])
        doms = Counter(scen_domain[sid] for sid in h['scenario_ids'])
        distractors += [doms[scen_domain[sid]] - 1 for sid in h['scenario_ids']]
        per_hist.append({'tokens': total, 'sessions': h['n_sessions'], 'turns': h['n_turns'],
                         'low_token_share': low / total, 'max_concurrent': max(starts.values())})
        for q in (q for q in qs if q['history_id'] == h['history_id'] and q['evidence']):
            idx = [pos[e] for e in q['evidence']]
            depth_first.append(suffix[min(idx)])
            depth_last.append(suffix[max(idx)])
    tok = [h['tokens'] for h in per_hist]
    return {
        'K': hists[0]['K'], 'histories': len(hists), 'questions': len(qs),
        'sessions_per_history': st.mean(h['sessions'] for h in per_hist),
        'turns_per_history': st.mean(h['turns'] for h in per_hist),
        'tokens_mean': st.mean(tok), 'tokens_min': min(tok), 'tokens_max': max(tok),
        'low_density_token_share': st.mean(h['low_token_share'] for h in per_hist),
        'same_domain_distractors': st.mean(distractors),
        'max_concurrent_sessions': max(h['max_concurrent'] for h in per_hist),
        'evidence_depth_first_mean': st.mean(depth_first),
        'evidence_depth_last_mean': st.mean(depth_last),
    }


def k_fmt(x):
    return f'{x / 1000:.1f}k' if x >= 1000 else f'{x:.0f}'


def render(stats):
    L = ['# M-LoCoMo 数据集统计', '',
         '> Token 单位：tiktoken `o200k_base`。“格式化 token”按 Full-context 提示逐行 `[timestamp] role: text` 计。', '']
    L += ['## 表 1 原始语料（100 场景，全部划分）', '',
          '| 指标 | ZH | EN |', '|---|---|---|']
    c = {lang: stats['corpus'][lang] for lang in LANGS}
    rows = [('场景数', 'scenarios', '{}'), ('Session 数', 'sessions', '{}'), ('轮次数', 'turns', '{}'),
            ('每 session 轮次', 'turns_per_session', '{:.1f}'), ('每场景轮次', 'turns_per_scenario', '{:.1f}'),
            ('每场景格式化 token', 'formatted_tokens_per_scenario', '{:.0f}'),
            ('问题数', 'questions', '{}'), ('其中不可回答', 'unanswerable_questions', '{}'),
            ('每题证据轮数（可回答题）', 'evidence_per_question', '{:.2f}'),
            ('低密度轮次占比', 'low_density_turn_ratio', '{:.1%}'), ('低密度 token 占比', 'low_density_token_ratio', '{:.1%}'),
            ('含 resolved_time 的轮次', 'turns_with_resolved_time', '{}'),
            ('含 resolved_shift 的轮次', 'turns_with_resolved_shift', '{}'),
            ('含 resolved_entities 的轮次', 'turns_with_resolved_entities', '{}')]
    for name, key, f in rows:
        L.append(f"| {name} | {f.format(c['zh'][key])} | {f.format(c['en'][key])} |")
    shifts = lambda lang: '，'.join(f'{k} {v}' for k, v in sorted(c[lang]['sessions_by_shift'].items()))
    L.append(f"| Session 所在班次 | {shifts('zh')} | {shifts('en')} |")
    L += ['', '## 表 2 按交互来源（turn_type）统计', '',
          '| turn_type | 轮次 ZH | token 占比 ZH | 低密度比例 ZH | 每轮 token ZH | 轮次 EN | token 占比 EN | 低密度比例 EN | 每轮 token EN |',
          '|---|---|---|---|---|---|---|---|---|']
    for tt in TURN_TYPES:
        z, e = c['zh']['by_turn_type'][tt], c['en']['by_turn_type'][tt]
        L.append(f"| {tt} | {z['turns']} | {z['token_share']:.1%} | {z['low_density_ratio']:.1%} | {z['tokens_per_turn']:.1f} "
                 f"| {e['turns']} | {e['token_share']:.1%} | {e['low_density_ratio']:.1%} | {e['tokens_per_turn']:.1f} |")
    L += ['', '## 表 3 问题类别（100 场景，ZH）', '',
          '| 类别 | 题数 | 难度 | 平均证据轮 | 平均所需事件 | 参考答案 token |', '|---|---|---|---|---|---|']
    for cat, v in c['zh']['by_category'].items():
        diff = '/'.join(f'{k}:{n}' for k, n in sorted(v['difficulty'].items()))
        L.append(f"| {cat} | {v['questions']} | {diff} | {v['evidence_turns']:.1f} | {v['required_events']:.1f} | {v['answer_tokens']:.1f} |")
    L += ['', '## 表 4 长历史版本 M-LoCoMo-Long', '',
          '评测池 eval = train + test（90 场景，2970 题）；dev = 10 场景（330 题），仅用于调参。',
          '“证据深度”= 从最早（或最晚）证据轮到历史末尾的格式化 token 数，衡量需要回溯多远。', '',
          '| 语言 | 池 | K | 历史数 | 题数 | 每历史 session | 每历史轮次 | 每历史 token 均值 [min–max] | 低密度 token 占比 | 同领域干扰场景 | 最大并发 session | 证据深度（最早） | 证据深度（最晚） |',
          '|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for key, h in stats['long'].items():
        lang, pool, _ = key.split('/')
        L.append(f"| {lang} | {pool} | {h['K']} | {h['histories']} | {h['questions']} | {h['sessions_per_history']:.0f} "
                 f"| {h['turns_per_history']:.0f} | {k_fmt(h['tokens_mean'])} [{k_fmt(h['tokens_min'])}–{k_fmt(h['tokens_max'])}] "
                 f"| {h['low_density_token_share']:.1%} | {h['same_domain_distractors']:.1f} | {h['max_concurrent_sessions']} "
                 f"| {k_fmt(h['evidence_depth_first_mean'])} | {k_fmt(h['evidence_depth_last_mean'])} |")
    return '\n'.join(L) + '\n'


if __name__ == '__main__':
    stats = {'corpus': {lang: corpus_stats(lang) for lang in LANGS}, 'long': {}}
    for d in sorted((ROOT / 'long').glob('*/*/K*')):
        stats['long'][f'{d.parts[-3]}/{d.parts[-2]}/{d.parts[-1]}'] = history_stats(d)
    (ROOT / 'long' / 'stats.json').write_text(json.dumps(stats, ensure_ascii=False, indent=2))
    md = render(stats)
    (ROOT / 'long' / 'STATS.md').write_text(md)
    print(md)
