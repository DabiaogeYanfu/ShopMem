"""The Reader's action format (方法论_v3_大纲 §3.4.2), the one definition of a valid action shared by
memeval.reader (episodes: format_ok, later r_fmt), memeval.reader_cold (which trajectories are clean)
and scripts/train_reader_sft.py (gen check). Standard library only: the SFT script imports it from
the sft env, without memeval's dependencies.

A valid reply is exactly one JSON object (surrounding whitespace aside: no prose, no Markdown fence)
with exactly these keys:
    {"reasoning": str, "action": "search", "query": non-empty str, "include_closed": JSON true / false}
    {"reasoning": str, "action": "answer", "answer": non-empty str}
"""
import json

KEYS = {'search': {'reasoning', 'action', 'query', 'include_closed'},
        'answer': {'reasoning', 'action', 'answer'}}


def _check(obj):
    if not isinstance(obj, dict) or not isinstance(obj.get('reasoning'), str):
        return None
    act = obj.get('action')
    if act not in KEYS or set(obj) != KEYS[act]:
        return None
    if act == 'search':
        query, closed = obj['query'], obj['include_closed']
        if not isinstance(query, str) or not query.strip() or not isinstance(closed, bool):
            return None
        return {'action': 'search', 'query': query.strip(), 'include_closed': closed}
    answer = obj['answer']
    if not isinstance(answer, str) or not answer.strip():
        return None
    return {'action': 'answer', 'answer': answer.strip()}


def parse_action(text, strict=True):
    """The reply's action ({'action': 'search', 'query', 'include_closed'} or {'action': 'answer', 'answer'}),
    None if it is not a valid one. strict=True: the whole reply must be the JSON object. strict=False
    (only to keep an episode going, never for format_ok): the span from the first '{' to the last '}',
    with the same field checks."""
    s = text.strip()
    if not strict:
        try:
            s = s[s.index('{'): s.rindex('}') + 1]
        except ValueError:
            return None
    try:
        return _check(json.loads(s))
    except json.JSONDecodeError:
        return None
