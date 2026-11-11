"""ShopMem-Reader (方法论_v3_大纲 §3.4): the read side as a retrieval-and-answer agent.

One episode per question:
1. The first retrieval is the current read pipeline unchanged: v1 retrieval over the Writer's bank,
   closed versions by the closed_view rule (memeval.methods.shopmem_writer). So with t_max=0 the
   Reader sees exactly the context the one-shot pipeline answers from.
2. The Reader replies with one JSON action per step:
     {"reasoning": ..., "action": "search", "query": ..., "include_closed": true|false}
     {"reasoning": ..., "action": "answer", "answer": ...}
   A search runs the same v1 retrieval with the Reader's own query; include_closed picks the view
   (true: closed versions too, each marked "no longer valid since ..."; false: valid units only).
   The result comes back as the next user message, with the number of searches left.
3. At most t_max searches. A valid action is defined once, in memeval.reader_format (the whole
   reply one JSON object with exactly the spec's keys). A reply that is not one strictly, but whose
   {...} span is (prose or a Markdown fence around it), is still executed so the episode goes on,
   and counts as a format error. A search past the budget, or a reply with no valid action at all,
   is answered with a "no more searches / give the answer as JSON" message and counts as a format
   error too; after t_max + 2 replies the episode ends and the last reply is read as the answer.
   format_ok = every reply strictly valid and the episode ended on an answer.

Search results are user messages (no tool role), so the conversation renders the same way when a
teacher writes it, when the SFT script trains on it, and when vLLM serves the trained Reader.
Used by memeval.reader_cold (teacher trajectories for the cold start), the shopmem_reader method
(evaluation through memeval.run) and later the GRPO rollouts.
"""
import time
from pathlib import Path

from .data import Query, read_jsonl
from .llm import count_tokens
from .memory_ops import MemoryBank, Unit
from .prompts import ANSWER_SYSTEM, parse_answer
from .reader_format import parse_action

TOOLS = (
    '\n4. Before answering you may search the memory again, at most {t_max} times. Reply with exactly one JSON '
    'object and nothing else (no other text, no Markdown), either\n'
    '{{"reasoning": "...", "action": "search", "query": "<what to look for>", "include_closed": true or false}}\n'
    'or\n'
    '{{"reasoning": "...", "action": "answer", "answer": "<final reply>"}}\n'
    'include_closed=true also returns entries that are no longer valid (earlier states, superseded values, '
    'constraints of closed work orders), each marked with the time it stopped being valid; false returns only '
    'what is valid at the current time. Search when the memory below is missing something the question needs, '
    'e.g. with an equipment / lot / work-order ID, a shift, or an earlier state; answer as soon as it is enough.')
FIRST = 'Memory (first retrieval for the question, include_closed={closed}):\n{context}\n\nQuestion: {question}'
RESULT = ('Search results for "{query}" (include_closed={closed}):\n{context}\n\n'
          'Searches left: {left}.')
EMPTY = '(nothing found)'
FORCE = 'No more searches. Give your final answer now: {"reasoning": "...", "action": "answer", "answer": "..."}'


def reader_system(as_of, calendar_text, t_max, hint=''):
    system = ANSWER_SYSTEM['en'].format(as_of=as_of, calendar=calendar_text)
    if t_max > 0:
        return system + TOOLS.format(t_max=t_max) + hint
    return (system + '\n4. Reply with exactly one JSON object and nothing else: '
            '{"reasoning": "...", "action": "answer", "answer": "<final reply>"}' + hint)


# ---------------------------------------------------------------- memory: the Writer's bank, read only
def banked_writer(llm, history, bank_file, method_config):
    """A ShopMemWriter over a finished bank file (no writing): its v1 retrieval, validity views, calendar.
    `llm` is the retrieval planner (the evaluation's 8B)."""
    from .methods.shopmem_writer import ShopMemWriter
    w = ShopMemWriter(llm, 'en', **method_config)
    w.setup(history)
    bank = MemoryBank()
    for d in read_jsonl(bank_file):
        u = Unit.from_dict(d)
        bank.units.append(u)
        bank.by_id[u.uid] = u
    w.bank, w._views = bank, {}
    return w


def search(memory, q, text, include_closed=None):
    """v1 retrieval with query text `text` at the question's as_of. include_closed=None: the closed_view
    rule decides (the first retrieval); True / False: that view. Request units (Writer request_units) are
    searched iff the original question is about roles, whatever the Reader's own query says."""
    query = Query(q.question_id, q.history_id, text, q.as_of)
    t0 = time.perf_counter()
    if include_closed is None:
        r = memory.retrieve(query)
        closed = bool(r.meta.get('historical'))
    else:
        view = memory._view(q.as_of, bool(include_closed), memory.wants_requests(q.question))
        r, closed = view.retrieve(query), bool(include_closed)
    r.meta['retrieve_s'] = time.perf_counter() - t0
    return r, closed


def bank_file(bank_dir, history_id):
    return Path(bank_dir) / f'{history_id}.jsonl'


# ---------------------------------------------------------------- one episode
def run_episode(llm, memory, q, t_max, hint='', **params):
    """Answer question q with the Reader `llm` over `memory` (banked_writer). `hint` is appended to the
    system prompt for this episode only (teacher generation); the returned messages carry the plain
    system prompt, so a trajectory is trained on the prompt the Reader is served with.
    Returns {messages, prediction, searches, format_ok, lenient_replies, provenance, context_tokens,
    prompt_tokens, steps, retrieve_s (time in retrieval, first retrieval included)}."""
    first, closed = search(memory, q, q.question)
    system = reader_system(q.as_of, memory.calendar_text, t_max)
    msgs = [{'role': 'system', 'content': system},
            {'role': 'user', 'content': FIRST.format(closed=str(closed).lower(), context=first.context or EMPTY,
                                                     question=q.question)}]
    run_msgs = [{'role': 'system', 'content': system + hint}] + msgs[1:]
    provenance, ctx_tokens, prompt_tokens = set(first.provenance), count_tokens(first.context), 0
    searches, format_ok, lenient, prediction, steps = [], True, 0, None, 0
    retrieve_s = first.meta['retrieve_s']
    for steps in range(1, t_max + 3):
        c = llm.complete(run_msgs, stage='answer', **params)
        prompt_tokens += c.prompt_tokens
        act = parse_action(c.text)
        if act is None:
            act = parse_action(c.text, strict=False)
            if act is not None:  # executed, but not a clean reply
                format_ok, lenient = False, lenient + 1
        reply = {'role': 'assistant', 'content': c.text}
        msgs.append(reply)
        run_msgs.append(reply)
        if act and act['action'] == 'answer':
            prediction = act['answer']
            break
        if act and act['action'] == 'search' and len(searches) < t_max:
            r, _ = search(memory, q, act['query'], act['include_closed'])
            searches.append({'query': act['query'], 'include_closed': act['include_closed'],
                             'entries': r.meta.get('entries', 0)})
            retrieve_s += r.meta['retrieve_s']
            provenance |= set(r.provenance)
            ctx_tokens += count_tokens(r.context)
            nxt = {'role': 'user', 'content': RESULT.format(query=act['query'], closed=str(act['include_closed']).lower(),
                                                            context=r.context or EMPTY, left=t_max - len(searches))}
        else:  # not a valid action, or a search past the budget
            format_ok = False
            nxt = {'role': 'user', 'content': FORCE}
        msgs.append(nxt)
        run_msgs.append(nxt)
    if prediction is None:
        format_ok = False
        last = next(m['content'] for m in reversed(msgs) if m['role'] == 'assistant')
        prediction = parse_answer(last)
    return {'messages': msgs, 'prediction': prediction, 'searches': searches, 'format_ok': format_ok,
            'lenient_replies': lenient, 'provenance': sorted(provenance), 'context_tokens': ctx_tokens,
            'prompt_tokens': prompt_tokens, 'steps': steps, 'retrieve_s': retrieve_s}
