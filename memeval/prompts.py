"""Answer and judge prompts shared by every method.

The answer prompt gives every method the same plant configuration: the site shift
calendars and the convention that a shift is named by its start date. Without it, a
correct "04-05 03:20 falls in the night shift that began 04-04 20:00" answer would
depend on guessing a convention, not on memory quality.
"""
import json
import re

# the "answer" field of a reasoning-format reply whose JSON does not parse (an unescaped quote or
# a raw newline inside the reasoning is enough); the answer field itself is short and ends the object
_ANSWER_FIELD = re.compile(r'"answer"\s*:\s*"((?:[^"\\]|\\.)*)"\s*\}?\s*(?:```)?\s*$', re.S)

ANSWER_SYSTEM = {
    'zh': (
        '你是工厂里的工业对话 Agent，根据下面提供的历史记忆回答同事的问题。\n'
        '要求：\n'
        '1. 只依据给出的历史记忆作答，不要编造。如果记忆里没有答案，直接回答“历史记录中没有相关信息”。\n'
        '2. 回答要简洁，但必须保留设备号、批次号、工单号、数值、时间和班次等关键细节。\n'
        '3. 问题涉及班次时，按生产日历换算；班次按其开始日期命名，例如 20:00 开始、次日 08:00 结束的夜班，'
        '记作“开始那天的夜班”。\n'
        '当前时间：{as_of}\n'
        '生产日历：\n{calendar}'
    ),
    'en': (
        'You are an industrial conversational agent in a factory. Answer your colleague\'s question using the '
        'memory provided below.\n'
        'Rules:\n'
        '1. Use only the memory provided; do not make anything up. If the memory does not contain the answer, '
        'reply "There is no record of this in the history."\n'
        '2. Be concise, but keep key details: equipment, lot and work-order IDs, values, times and shifts.\n'
        '3. For questions about shifts, use the production calendar. A shift is named by the date it starts, e.g. '
        'a night shift running 20:00 to 08:00 the next day is "the night shift of the day it started".\n'
        'Current time: {as_of}\n'
        'Production calendar:\n{calendar}'
    ),
}

ANSWER_USER = {
    'zh': '历史记忆：\n{context}\n\n问题：{question}\n回答：',
    'en': 'Memory:\n{context}\n\nQuestion: {question}\nAnswer:',
}

NEXT_DAY = {'zh': '次日', 'en': 'next day '}


def render_calendar(calendars, lang):
    by_cal = {}
    for site, cal in sorted(calendars['site_calendar'].items()):
        by_cal.setdefault(cal, []).append(site)
    lines = []
    for cal, sites in by_cal.items():
        shifts = []
        for s in calendars['calendars'][cal]:
            h, m = map(int, s['start'].split(':'))
            end_min = (h * 60 + m + s['hours'] * 60)
            end = f'{(end_min // 60) % 24:02d}:{end_min % 60:02d}'
            shifts.append(f"{s['shift']} {s['start']}–{NEXT_DAY[lang] if end_min >= 1440 else ''}{end}")
        lines.append(f"- {', '.join(sites)}: {'; '.join(shifts)}")
    return '\n'.join(lines)


# --answer-format reasoning (every method alike): reason first, then answer, as SimpleMem's
# AnswerGenerator does. Only the "answer" field is scored.
ANSWER_REASONING = {
    'zh': '\n4. 先在 reasoning 中简要推理（依据哪几条记忆、时间与班次如何对应），再在 answer 中给出最终回答。'
          '只输出 JSON：{"reasoning": "...", "answer": "..."}',
    'en': '\n4. First reason briefly in "reasoning" (which memory entries apply, how times map to shifts), then give '
          'the final reply in "answer". Output JSON only: {"reasoning": "...", "answer": "..."}',
}


def answer_messages(lang, context, question, as_of, calendar_text, reasoning=False):
    system = ANSWER_SYSTEM[lang].format(as_of=as_of, calendar=calendar_text)
    if reasoning:
        system += ANSWER_REASONING[lang]
    return [{'role': 'system', 'content': system},
            {'role': 'user', 'content': ANSWER_USER[lang].format(context=context, question=question)}]


def parse_answer(text):
    """The "answer" field of a reasoning-format reply; the raw text if it is not that JSON."""
    try:
        obj = json.loads(text[text.index('{'): text.rindex('}') + 1])
        answer = obj.get('answer') if isinstance(obj, dict) else None
    except (ValueError, json.JSONDecodeError):
        m = _ANSWER_FIELD.search(text)
        # strict=False: replies may carry raw newlines inside the answer string
        answer = json.loads(f'"{m.group(1)}"', strict=False) if m else None
    if isinstance(answer, list):
        answer = '；'.join(map(str, answer))
    return str(answer).strip() if answer else text.strip()


# One English judge prompt for both languages: the rules are about content, not wording.
JUDGE_SYSTEM = (
    'You grade answers of a factory assistant against a gold answer. Output JSON only: '
    '{"reason": "<one sentence>", "label": "CORRECT" or "WRONG"}.'
)

JUDGE_RULES = {
    'default': (
        'CORRECT if the generated answer conveys the facts of the gold answer that answer the question. Every '
        'entity, value, threshold, date, outcome and order the generated answer states must agree with the gold '
        'answer: a wrong or contradicting one makes it WRONG. Wording may differ. Incidental details of the gold '
        'answer that the question does not ask for (the tool name, the time a measurement was taken, an ID the '
        'question already names) may be omitted. Extra detail is fine unless it contradicts the gold answer. If '
        'the gold answer lists several items (steps, measures, constraints, facts), every item must be present in '
        'substance.'
    ),
    'shift_temporal': (
        'The gold answer names a production shift by the date the shift STARTS (a night shift that starts '
        'on 04-04 and ends on 04-05 is "04-04 night shift"). CORRECT if the generated answer identifies the same '
        'shift: the same shift name and start date, or a date/time range that unambiguously lies inside that '
        'shift. A bare timestamp without naming the shift, or the right shift name on the wrong date, is WRONG.'
    ),
    'unanswerable': (
        'The question cannot be answered from the history (the gold answer says no record exists). CORRECT only '
        'if the generated answer says the information is not available / not recorded, or rejects the false '
        'premise. Any substantive answer (a value, an entity, an event) is WRONG.'
    ),
}

JUDGE_USER = 'Grading rule: {rule}\n\nQuestion: {question}\nGold answer: {gold}\nGenerated answer: {pred}'


def judge_messages(category, question, gold, pred):
    rule = JUDGE_RULES.get(category, JUDGE_RULES['default'])
    return [{'role': 'system', 'content': JUDGE_SYSTEM},
            {'role': 'user', 'content': JUDGE_USER.format(rule=rule, question=question, gold=gold, pred=pred)}]


# Batched judging (--judge-batch n): several items of ONE category per request, so the
# grading rule is stated once and the request count drops n-fold. Each item is judged on
# its own; the reply carries one verdict per item id.
JUDGE_BATCH_SYSTEM = (
    'You grade answers of a factory assistant against gold answers. Judge every item independently of the '
    'others. Output JSON only: {"results": [{"id": <item id>, "reason": "<one sentence>", "label": "CORRECT" or '
    '"WRONG"}, ...]} with exactly one result per item.'
)
JUDGE_BATCH_ITEM = '### Item {id}\nQuestion: {question}\nGold answer: {gold}\nGenerated answer: {pred}'


def judge_batch_messages(category, items):
    """items: [(id, question, gold, pred)], all of one category."""
    rule = JUDGE_RULES.get(category, JUDGE_RULES['default'])
    body = '\n\n'.join(JUDGE_BATCH_ITEM.format(id=i, question=q, gold=g, pred=p) for i, q, g, p in items)
    return [{'role': 'system', 'content': JUDGE_BATCH_SYSTEM},
            {'role': 'user', 'content': f'Grading rule: {rule}\n\n{body}'}]
