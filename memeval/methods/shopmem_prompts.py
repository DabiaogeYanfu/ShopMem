"""Prompts for ShopMem (memeval.methods.shopmem): extraction (gate + Φ), synthesis, retrieval planning.

Each prompt exists in zh and en. Ablation variants are small clauses swapped in by
`extraction_messages`, so the ablated and full prompts differ only in the clause the
ablation is about.
"""
import json

# ISA-95 (IEC 62264) object classes used by the symbolic layer; a subset of ontology/entity_types.yaml
ISA95_TYPES = ['Equipment', 'Component', 'MaterialLot', 'Product', 'WorkOrder', 'Personnel', 'QualityTest',
               'WorkCenter']

EXTRACT_SYSTEM = {
    'zh': (
        '你是工厂工业对话 Agent 的记忆写入模块。给你一段交互窗口（操作人员输入、Agent 回复、Agent 思考、工具调用返回），'
        '把其中值得长期保存的信息提取成独立的记忆单元。\n'
        '{gate_rule}\n'
        '每个记忆单元的要求：\n'
        '1. 最小语义单元：一条单元只写一件事（一次报警、一个判断、一次处置及其结果、一条约束、一次工具核实的关键结果等）。\n'
        '2. {entity_rule}\n'
        '3. 时间：若内容提到“刚才”“上一班”“昨天”“15:57”等时间，填写 time_ref：'
        '"previous_shift"（上一班/上个班次）、"current_shift"（本班/这个班），或具体时间 "YYYY-MM-DD HH:MM"；未提到则为 null。\n'
        '4. 必须原样保留设备号、部件号、批次号、工单号、报警代码、数值、单位、阈值、工具名和检测时间，不要概括掉。'
        '工具返回中的关键异常或验证结果要写明是哪个工具返回的。\n'
        '5. 约束条件（如“验证完成前不得关闭任务”）和待办事项要与事件分开、原样保留：约束以“约束：”开头，待办以“待办：”开头，'
        '写明所针对的工单号或设备号；同一句话里列出的多条约束放在同一个单元里，不要拆开。\n'
        '6. turns 填写该单元依据的轮次编号（窗口内的 #编号）。\n'
        '只输出 JSON：{{"units": [{{"text": "...", "turns": [1, 2], "entities": [{entity_schema}], '
        '"time_ref": null}}]}}。没有值得保存的信息时输出 {{"units": []}}。'
    ),
    'en': (
        'You are the memory-writing module of an industrial conversational agent in a factory. Given a window of '
        'interaction (operator input, agent replies, agent thoughts, tool call returns), extract the information '
        'worth keeping long term as self-contained memory units.\n'
        '{gate_rule}\n'
        'Each memory unit must:\n'
        '1. Be a minimal semantic unit: one thing per unit (one alarm, one hypothesis, one action and its outcome, '
        'one constraint, the key result of one tool check, ...).\n'
        '2. {entity_rule}\n'
        '3. Time: if the content refers to a time ("just now", "the previous shift", "yesterday", "15:57"), fill '
        'time_ref with "previous_shift", "current_shift", or an explicit "YYYY-MM-DD HH:MM"; otherwise null.\n'
        '4. Keep equipment, component, lot and work-order IDs, alarm codes, values, units, thresholds, tool names '
        'and measurement times verbatim; do not summarize them away. For a key abnormal or verification result '
        'returned by a tool, state which tool returned it.\n'
        '5. Constraints (e.g. "do not close the task before verification") and open items are kept apart from '
        'events and verbatim: a constraint starts with "Constraint:", an open item with "Open item:", and names the '
        'work order or equipment it applies to; several constraints listed in one sentence stay in one unit.\n'
        '6. turns lists the turn numbers (#n in the window) the unit is based on.\n'
        'Output JSON only: {{"units": [{{"text": "...", "turns": [1, 2], "entities": [{entity_schema}], '
        '"time_ref": null}}]}}. If nothing is worth keeping, output {{"units": []}}.'
    ),
}

GATE_RULE = {
    'llm': {
        'zh': '只保存带来新信息的内容：新的事实、状态变化、异常或关键测量、判断、处置及结果、约束和待办。'
              '人员提出的怀疑、初步判断或假设即使尚未确认也要保存，并写明“尚未确认”。'
              'Agent 核实后给出的结论即使与之前人员的报告内容相同也要保存，写明“经核实确认”，它代表已确认的最新状态。'
              '寒暄、重复确认、过程性衔接、没有实际结果的思考、结果为“正常/无异常”的常规工具查询都不要保存。',
        'en': 'Keep only content that adds information: new facts, state changes, abnormal or key measurements, '
              'hypotheses, actions and their outcomes, constraints and open items. A suspicion, preliminary '
              'judgement or hypothesis raised by a person is kept even if unconfirmed, marked as unconfirmed. A '
              'conclusion the agent gives after verification is kept even if it repeats an earlier report, marked as '
              'verified: it is the confirmed latest state. Do not '
              'keep small talk, repeated acknowledgements, procedural filler, thoughts with no actual result, or '
              'routine tool checks that returned "normal / nothing abnormal".',
    },
    'keep_all': {  # ablation N1a: no gating, every turn is kept
        'zh': '不要做任何过滤：窗口中每一轮（包括寒暄、思考和所有工具返回）都要提取为记忆单元。',
        'en': 'Do not filter anything: every turn in the window (including small talk, thoughts and every tool '
              'return) must become a memory unit.',
    },
}

ENTITY_RULE = {
    'isa95': {
        'zh': '可独立理解：把“它”“这台设备”“那批料”“这个传感器”等指代替换成具体、完整的标识（设备号、批次号、工单号、'
              '部件名+编号），可参考“已知实体”列表。entities 列出单元涉及的实体，id 用对话中出现的完整标识，'
              'type 取 ISA-95 类别之一：' + ' / '.join(ISA95_TYPES) + '。',
        'en': 'Be self-contained: replace references such as "it", "this machine", "that lot", "the sensor" with '
              'the concrete full identifier (equipment ID, lot ID, work-order ID, component name + number); the '
              '"known entities" list may help. entities lists the entities the unit involves: id is the full '
              'identifier as it appears in the dialogue, type is one ISA-95 class: ' + ' / '.join(ISA95_TYPES) + '.',
    },
    'open': {  # ablation N2: open-domain entity names, no ISA-95 identifiers or classes
        'zh': '可独立理解：把“它”“这台设备”等指代替换成对象的名称。entities 列出单元涉及的对象，'
              'id 为对象名称，type 为你认为合适的类别。',
        'en': 'Be self-contained: replace references such as "it" or "this machine" with the name of the object. '
              'entities lists the objects the unit involves: id is the object name, type is any category you see fit.',
    },
}

ENTITY_SCHEMA = '{"id": "...", "type": "..."}'

EXTRACT_USER = {
    'zh': '站点：{site}\n生产日历：{calendar}\n已知实体（本站点此前出现过的标识，越靠前越近）：{known}\n'
          '窗口之前的对话：\n{history}\n\n当前窗口：\n{window}',
    'en': 'Site: {site}\nProduction calendar: {calendar}\nKnown entities (identifiers seen earlier at this site, most '
          'recent first): {known}\nDialogue before the window:\n{history}\n\nCurrent window:\n{window}',
}

NONE = {'zh': '无', 'en': 'none'}

# One worked example (borrowed from SimpleMem's few-shot extraction prompt), written for the
# industrial case: a routine "normal" tool return, a think turn and an acknowledgement are
# gated out, "上一班" is marked, an unconfirmed hypothesis is kept, a two-part constraint stays
# one unit. Its IDs (CNC-M2-015, WO_0015) do not occur in M-LoCoMo. Units carry `gated` so the
# keep_all ablation renders the same example without filtering; entities carry an ISA-95 class
# and an open-domain category for the entity_norm ablation.
EXAMPLE_WINDOW = {
    'zh': ['[2026-03-10T09:12:00+08:00] P201 (operator, user_query): 上一班值班期间，CNC-M2-015主轴有异响，麻烦查一下。',
           '[2026-03-10T09:13:00+08:00] AGENT (industrial_agent, agent_think): 我先查温度，再看振动。',
           '[2026-03-10T09:14:00+08:00] AGENT (industrial_agent, agent_tool_call:temperature_query): '
           'temperature_query返回：CNC-M2-015主轴温度61°C，处于正常区间（≤75°C）。',
           '[2026-03-10T09:15:00+08:00] AGENT (industrial_agent, agent_tool_call:vibration_query): '
           'vibration_query返回：CNC-M2-015振动值4.1mm/s，超过报警阈值2.5mm/s，检测时间09:15。',
           '[2026-03-10T09:16:00+08:00] P305 (maintenance_engineer, human): 怀疑是它的主轴轴承#015磨损，还没确认。',
           '[2026-03-10T09:17:00+08:00] P201 (operator, human): 好的，收到。',
           '[2026-03-10T09:18:00+08:00] P305 (maintenance_engineer, human): '
           '提醒两条约束：主轴轴承#015更换前CNC-M2-015不得恢复生产；工单WO_0015暂不关闭。'],
    'en': ['[2026-03-10T09:12:00+08:00] P201 (operator, user_query): During the previous shift CNC-M2-015 made a '
           'noise at the spindle, can you check?',
           '[2026-03-10T09:13:00+08:00] AGENT (industrial_agent, agent_think): I will check temperature first, then '
           'vibration.',
           '[2026-03-10T09:14:00+08:00] AGENT (industrial_agent, agent_tool_call:temperature_query): '
           'temperature_query returned: CNC-M2-015 spindle temperature 61°C, within the normal range (<=75°C).',
           '[2026-03-10T09:15:00+08:00] AGENT (industrial_agent, agent_tool_call:vibration_query): '
           'vibration_query returned: CNC-M2-015 vibration 4.1mm/s, above the alarm threshold 2.5mm/s, measured at '
           '09:15.',
           '[2026-03-10T09:16:00+08:00] P305 (maintenance_engineer, human): I suspect its spindle bearing #015 is '
           'worn, not confirmed yet.',
           '[2026-03-10T09:17:00+08:00] P201 (operator, human): OK, got it.',
           '[2026-03-10T09:18:00+08:00] P305 (maintenance_engineer, human): Two constraints: CNC-M2-015 must not '
           'resume production before spindle bearing #015 is replaced; work order WO_0015 stays open.'],
}
_EQ = ('CNC-M2-015', 'Equipment', {'zh': '机床', 'en': 'machine'})
_BEARING = ({'zh': '主轴轴承#015', 'en': 'spindle bearing #015'}, 'Component', {'zh': '零件', 'en': 'part'})
_WO = ('WO_0015', 'WorkOrder', {'zh': '工单', 'en': 'work order'})
EXAMPLE_UNITS = [
    {'turns': [1], 'time_ref': 'previous_shift', 'entities': [_EQ],
     'text': {'zh': '操作员报告：CNC-M2-015主轴有异响。', 'en': 'Operator reported: CNC-M2-015 made a noise at the spindle.'}},
    {'turns': [2], 'gated': True, 'entities': [_EQ],
     'text': {'zh': 'Agent计划先查CNC-M2-015温度，再看振动。',
              'en': 'The agent planned to check CNC-M2-015 temperature, then vibration.'}},
    {'turns': [3], 'gated': True, 'entities': [_EQ],
     'text': {'zh': 'temperature_query返回：CNC-M2-015主轴温度61°C，处于正常区间（≤75°C）。',
              'en': 'temperature_query returned: CNC-M2-015 spindle temperature 61°C, within the normal range (<=75°C).'}},
    {'turns': [4], 'entities': [_EQ],
     'text': {'zh': 'vibration_query返回：CNC-M2-015振动值4.1mm/s，超过报警阈值2.5mm/s，检测时间09:15。',
              'en': 'vibration_query returned: CNC-M2-015 vibration 4.1mm/s, above the alarm threshold 2.5mm/s, '
                    'measured at 09:15.'}},
    {'turns': [5], 'entities': [_BEARING, _EQ],
     'text': {'zh': '维修工程师怀疑CNC-M2-015的主轴轴承#015磨损，尚未确认。',
              'en': 'The maintenance engineer suspects spindle bearing #015 of CNC-M2-015 is worn; not confirmed.'}},
    {'turns': [6], 'gated': True, 'entities': [],
     'text': {'zh': '操作员表示收到。', 'en': 'The operator acknowledged.'}},
    {'turns': [7], 'entities': [_EQ, _BEARING, _WO],
     'text': {'zh': '约束（WO_0015 / CNC-M2-015）：主轴轴承#015更换前CNC-M2-015不得恢复生产；工单WO_0015暂不关闭。',
              'en': 'Constraint (WO_0015 / CNC-M2-015): CNC-M2-015 must not resume production before spindle bearing '
                    '#015 is replaced; work order WO_0015 stays open.'}},
]
EXAMPLE_HEADER = {'zh': '示例窗口：', 'en': 'Example window:'}
EXAMPLE_OUTPUT = {'zh': '示例输出：', 'en': 'Example output:'}
EXAMPLE_NOTE = {'zh': '（#2 思考、#3 结果正常的工具查询、#6 应答没有新信息，不保存。）',
                'en': '(#2 thought, #3 routine normal tool check and #6 acknowledgement add nothing and are not kept.)'}


def _pick(value, lang):
    return value[lang] if isinstance(value, dict) else value


def render_example(lang, gate, entity_norm):
    units = []
    for u in EXAMPLE_UNITS:
        if u.get('gated') and gate != 'keep_all':
            continue
        ents = [{'id': _pick(i, lang), 'type': t if entity_norm else _pick(o, lang)} for i, t, o in u['entities']]
        units.append({'text': u['text'][lang], 'turns': u['turns'], 'entities': ents,
                      'time_ref': u.get('time_ref')})
    window = '\n'.join(f'#{i} {line}' for i, line in enumerate(EXAMPLE_WINDOW[lang], 1))
    note = '' if gate == 'keep_all' else '\n' + EXAMPLE_NOTE[lang]
    return (f'\n\n{EXAMPLE_HEADER[lang]}\n{window}\n{EXAMPLE_OUTPUT[lang]}\n'
            f'{json.dumps({"units": units}, ensure_ascii=False)}{note}')


def extraction_messages(lang, gate, entity_norm, site, calendar, known, history, window, example=True):
    entity_mode = 'isa95' if entity_norm else 'open'
    system = EXTRACT_SYSTEM[lang].format(gate_rule=GATE_RULE[gate][lang], entity_rule=ENTITY_RULE[entity_mode][lang],
                                         entity_schema=ENTITY_SCHEMA)
    if example:
        system += render_example(lang, gate, entity_norm)
    user = EXTRACT_USER[lang].format(site=site, calendar=calendar, known=', '.join(known) or NONE[lang],
                                     history=history or NONE[lang], window=window)
    return [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]


SYNTH_SYSTEM = {
    'zh': (
        '你是工厂工业对话 Agent 的记忆整合模块。下面是同一次会话中新提取的记忆单元（带编号和证据来源）。'
        '把描述同一对象、同一事件或同一交互过程的单元整合为一条连贯、信息完整的记忆条目：\n'
        '1. 内容重复的单元只保留信息最完整的表述；\n'
        '2. 同一过程的不同环节（如人工报告与随后的工具核实）合成一条，{source_rule}\n'
        '3. 必须保留所有设备号、批次号、工单号、报警代码、数值、阈值、工具名和时间，不得丢失任何事实；\n'
        '4. 约束条件和待办事项不要与事件合并，保持单独成条；\n'
        '5. 与其他单元无关的单元原样保留为单独一条。\n'
        '每个编号必须且只能出现在一个条目的 from 中。只输出 JSON：{{"entries": [{{"from": [1, 3], "text": "..."}}]}}'
    ),
    'en': (
        'You are the memory-consolidation module of an industrial conversational agent. Below are the memory units '
        'newly extracted from one session (numbered, with their evidence sources). Merge units that describe the '
        'same object, event or interaction into one coherent, complete memory entry:\n'
        '1. For units that repeat the same content, keep only the most complete statement;\n'
        '2. Merge different steps of one process (e.g. an operator report and the tool check that followed) into '
        'one entry, {source_rule}\n'
        '3. Keep every equipment, lot and work-order ID, alarm code, value, threshold, tool name and time; no fact '
        'may be lost;\n'
        '4. Do not merge constraints or open items into events; keep them as separate entries;\n'
        '5. Keep a unit unrelated to the others as its own entry, unchanged.\n'
        'Every number must appear in the "from" of exactly one entry. Output JSON only: '
        '{{"entries": [{{"from": [1, 3], "text": "..."}}]}}'
    ),
}

SOURCE_RULE = {
    True: {'zh': '并在文字中写明各部分来自哪类证据（人工报告 / 工具核实，以及工具名）；',
           'en': 'and state in the text which kind of evidence each part comes from (operator report / tool check, '
                 'with the tool name);'},
    False: {'zh': '写成一条连贯的叙述；', 'en': 'written as one coherent account;'},  # ablation N4
}

SYNTH_USER = {'zh': '记忆单元：\n{units}', 'en': 'Memory units:\n{units}'}


def synthesis_messages(lang, keep_sources, units):
    system = SYNTH_SYSTEM[lang].format(source_rule=SOURCE_RULE[keep_sources][lang])
    return [{'role': 'system', 'content': system}, {'role': 'user', 'content': SYNTH_USER[lang].format(units=units)}]


PLAN_SYSTEM = {
    'zh': (
        '你是工厂工业对话 Agent 的检索规划模块。记忆库中的每条记忆有三种检索视图：\n'
        '- semantic：稠密向量语义检索，适合抽象描述、过程、原因、多事件聚合；\n'
        '- lexical：BM25 关键词检索，适合设备号、批次号、工单号、报警代码、工具名等精确字面匹配；\n'
        '- symbolic：按实体标识、ISA-95 类别（' + ' / '.join(ISA95_TYPES) + '）和时间过滤，'
        '可按时间取最早或最新的记录。\n'
        '分析问题的信息需求，为三种视图分别给出查询和检索深度 depth（1–5，1 表示只需一两条，5 表示需要大量记录）。'
        'semantic_queries 给 1–4 条语义查询：第一条是问题本身的改写，问题涉及多个环节或多个事件时'
        '（如最初的判断与后来的更新、报告与验证、整个处理过程），为每个环节各写一条有针对性的查询。'
        '三个深度要分别判断，不要都填同一个值：例如核实某设备最新状态主要依赖 symbolic 和 lexical，'
        '而梳理整个处理过程或比较多个事件则需要更深的 semantic。\n'
        '只输出 JSON：{{"semantic_queries": ["..."], "keywords": ["..."], "entities": ["问题中出现的完整标识"], '
        '"entity_types": [], "time_from": null, "time_to": null, "order": "earliest" | "latest" | "all", '
        '"depth": {{"semantic": 1, "lexical": 1, "symbolic": 1}}}}\n'
        'time_from / time_to 为 "YYYY-MM-DD" 或 null；order 表示问题要最早的记录、最新的记录，还是不限。'
    ),
    'en': (
        'You are the retrieval-planning module of an industrial conversational agent. Every memory entry has three '
        'retrieval views:\n'
        '- semantic: dense-vector search, for abstract descriptions, processes, causes, aggregating several events;\n'
        '- lexical: BM25 keyword search, for exact matches on equipment, lot and work-order IDs, alarm codes, tool '
        'names;\n'
        '- symbolic: filter by entity identifier, ISA-95 class (' + ' / '.join(ISA95_TYPES) + ') and time, and take '
        'the earliest or latest records.\n'
        'Analyse what the question needs and give each view its own query and retrieval depth (1-5; 1 = one or two '
        'entries suffice, 5 = many entries are needed). semantic_queries holds 1-4 semantic queries: the first '
        'restates the question; when the question spans several steps or events (an early judgement and its '
        'later update, a report and its verification, a whole process), add one targeted query per step. Judge the three depths separately rather than giving them '
        'one value: e.g. checking the latest state of a machine relies mostly on symbolic and lexical, while '
        'tracing a whole process or comparing several events needs a deeper semantic search.\n'
        'Output JSON only: {{"semantic_queries": ["..."], "keywords": ["..."], "entities": ["full identifiers in the '
        'question"], "entity_types": [], "time_from": null, "time_to": null, "order": "earliest" | "latest" | '
        '"all", "depth": {{"semantic": 1, "lexical": 1, "symbolic": 1}}}}\n'
        'time_from / time_to are "YYYY-MM-DD" or null; order says whether the question wants the earliest records, '
        'the latest, or either.'
    ),
}

SHARED_DEPTH_CLAUSE = {  # ablation N5: one depth for all views (original SimpleMem)
    'zh': '\n注意：三种视图使用同一个检索深度，depth 三项必须填同一个值。',
    'en': '\nNote: all three views share one retrieval depth; the three depth values must be equal.',
}

PLAN_USER = {'zh': '当前时间：{as_of}\n问题：{question}', 'en': 'Current time: {as_of}\nQuestion: {question}'}


def planning_messages(lang, shared_depth, question, as_of):
    system = PLAN_SYSTEM[lang].format() + (SHARED_DEPTH_CLAUSE[lang] if shared_depth else '')
    return [{'role': 'system', 'content': system},
            {'role': 'user', 'content': PLAN_USER[lang].format(as_of=as_of, question=question)}]


REFLECT_SYSTEM = {  # one round of SimpleMem-style sufficiency reflection (optional, reflect=true)
    'zh': '你是工厂工业对话 Agent 的检索检查模块。判断下面检索到的记忆是否足以完整回答问题。'
          '若不足，给出 1–3 条用于补充检索的查询，针对缺失的信息。'
          '只输出 JSON：{{"sufficient": true 或 false, "queries": ["..."]}}',
    'en': 'You are the retrieval-checking module of an industrial conversational agent. Decide whether the retrieved '
          'memory below is enough to answer the question completely. If not, give 1-3 follow-up search queries '
          'targeting the missing information. Output JSON only: {{"sufficient": true or false, "queries": ["..."]}}',
}
REFLECT_USER = {'zh': '问题：{question}\n\n已检索的记忆：\n{context}',
                'en': 'Question: {question}\n\nRetrieved memory:\n{context}'}


def reflection_messages(lang, question, context):
    return [{'role': 'system', 'content': REFLECT_SYSTEM[lang].format()},
            {'role': 'user', 'content': REFLECT_USER[lang].format(question=question, context=context or NONE[lang])}]


def parse_json(text):
    """The outermost {...} of a model reply as a dict, or None."""
    try:
        obj = json.loads(text[text.index('{'): text.rindex('}') + 1])
    except (ValueError, json.JSONDecodeError):
        return None
    return obj if isinstance(obj, dict) else None
