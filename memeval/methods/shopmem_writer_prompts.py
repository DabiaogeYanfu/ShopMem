"""Prompts of ShopMem-Writer (memeval.methods.shopmem_writer): extractor and manager.

One model plays both roles; only the prompt differs. English only (v2 trains and evaluates
on the English data). Both roles answer in JSON Lines ending with END_MARK. The worked
example's identifiers (CNC-M2-915, WO_9015, spindle bearing #915) do not occur in M-LoCoMo,
and the example passes the r_ind checks (tests/test_writer.py).
"""
import json

from ..memory_ops import END_MARK

EXTRACT_SYSTEM = (
    'You are the fact extractor of an industrial agent\'s long-term memory. You read one window of the agent\'s '
    'interaction log in a factory -- operator messages, the agent\'s thoughts and replies, the agent\'s tool calls and '
    'the JSON records the plant systems (EMS / MES / QMS / WMS) return -- and write down the facts worth '
    'remembering for later questions.\n'
    'Keep: new facts, state changes, abnormal or decisive measurements, hypotheses (say they are unconfirmed), '
    'actions and their outcomes, the agent\'s verified conclusions, constraints and open items. Skip small talk, '
    'acknowledgements, plans without a result, and routine tool checks whose values are normal.\n'
    'Each fact is one JSON object with these fields:\n'
    '- "content": one self-contained statement. Replace "it", "this machine", "that lot" with the full identifier. '
    'Keep identifiers, alarm codes, values, units, thresholds and tool names verbatim. A constraint starts with '
    '"Constraint:", an open item with "Open item:", and names the work order or equipment it applies to.\n'
    '- "entities": the identifiers the fact is about, exactly as written in the log (equipment, lot, work order, '
    'alarm code, component such as "temperature sensor #081"); not people or roles; [] if none.\n'
    '- "time": when the event happened, absolute "YYYY-MM-DD HH:MM": the measured_at of a tool record, or a clock '
    'time stated in the log; null if only the shift is known.\n'
    '- "shift": the production shift the event happened in, "YYYY-MM-DD <shift name>", named by the date the shift '
    'starts. Every window turn shows the shift it was sent in. "The previous shift" / "the last shift" / a shift '
    'handoff means the shift before that one (see the production calendar); an event with a time (e.g. a tool '
    'record measured earlier) is in the shift containing that time.\n'
    '- "source": "human" (a person said it), "tool" (a plant-system record), or "agent" (the agent\'s own '
    'conclusion).\n'
    '- "turns": the #numbers of the window turns the fact rests on.\n'
    'Output one JSON object per line, then a last line ' + END_MARK + '. If nothing in the window is worth keeping, '
    'output only ' + END_MARK + '.'
)

EXAMPLE_WINDOW = [
    '[2026-03-10T09:12:00+08:00] P201 (operator, user_query): During the previous shift CNC-M2-915 made a noise at '
    'the spindle, can you check?',
    '[2026-03-10T09:13:00+08:00] AGENT (industrial_agent, agent_think): I will check temperature first, then vibration.',
    '[2026-03-10T09:13:00+08:00] AGENT (industrial_agent, agent_tool_call:temperature_query): {"tool": '
    '"temperature_query", "args": {"equipment": "CNC-M2-915", "window": "24h"}}',
    '[2026-03-10T09:14:00+08:00] SYS_EMS (system_tool, tool_observation:temperature_query): {"tool": '
    '"temperature_query", "status": "success", "target": {"equipment": "CNC-M2-915"}, "measured_at": "2026-03-10 '
    '09:14", "data": {"spindle_temp_c": 61.0}, "limits": {"spindle_temp_c_max": 75}, "result": "normal", "message": '
    '"CNC-M2-915 spindle temperature 61.0°C, within normal range (<=75°C)."}',
    '[2026-03-10T09:14:00+08:00] AGENT (industrial_agent, agent_tool_call:vibration_query): {"tool": '
    '"vibration_query", "args": {"equipment": "CNC-M2-915", "window": "24h"}}',
    '[2026-03-10T09:15:00+08:00] SYS_EMS (system_tool, tool_observation:vibration_query): {"tool": "vibration_query", '
    '"status": "success", "target": {"equipment": "CNC-M2-915"}, "measured_at": "2026-03-10 09:15", "data": '
    '{"vibration_rms_mm_s": 4.1}, "limits": {"alarm_threshold_mm_s": 2.5}, "result": "abnormal", "message": '
    '"CNC-M2-915 vibration at 4.1mm/s, above the 2.5mm/s alarm threshold."}',
    '[2026-03-10T09:16:00+08:00] P305 (maintenance_engineer, human): I suspect its spindle bearing #915 is worn, not '
    'confirmed yet.',
    '[2026-03-10T09:17:00+08:00] P201 (operator, human): OK, got it.',
    '[2026-03-10T09:18:00+08:00] P305 (maintenance_engineer, human): Two constraints: CNC-M2-915 must not resume '
    'production before spindle bearing #915 is replaced; work order WO_9015 stays open.',
]
EXAMPLE_SITE = 'SITE_01'  # three shifts: Day 07:30, Swing 15:30, Night 23:30
EXAMPLE_SHIFT = '2026-03-10 Day shift'  # the shift every example turn was sent in
EXAMPLE_FACTS = [
    {'content': 'The operator reported that CNC-M2-915 made a noise at the spindle during the previous shift.',
     'entities': ['CNC-M2-915'], 'time': None, 'shift': '2026-03-09 Night shift', 'source': 'human', 'turns': [1]},
    {'content': 'vibration_query: CNC-M2-915 vibration 4.1 mm/s, above the 2.5 mm/s alarm threshold.',
     'entities': ['CNC-M2-915'], 'time': '2026-03-10 09:15', 'shift': '2026-03-10 Day shift', 'source': 'tool',
     'turns': [5, 6]},
    {'content': 'The maintenance engineer suspects spindle bearing #915 of CNC-M2-915 is worn; not confirmed yet.',
     'entities': ['spindle bearing #915', 'CNC-M2-915'], 'time': None, 'shift': '2026-03-10 Day shift',
     'source': 'human', 'turns': [7]},
    {'content': 'Constraint (WO_9015 / CNC-M2-915): CNC-M2-915 must not resume production before spindle bearing '
                '#915 is replaced; work order WO_9015 stays open.',
     'entities': ['CNC-M2-915', 'spindle bearing #915', 'WO_9015'], 'time': None, 'shift': '2026-03-10 Day shift',
     'source': 'human', 'turns': [9]},
]
EXAMPLE_NOTE = '(#2 is a plan, #3-#4 a routine check with normal values, #8 an acknowledgement: not kept.)'


def jsonl(objs):
    return ''.join(json.dumps(o, ensure_ascii=False) + '\n' for o in objs) + END_MARK


def window_line(i, shift, rendered_turn):
    """A numbered window turn with the production shift it was sent in (plant configuration, as an MES stamps it)."""
    return f'#{i} (shift: {shift}) {rendered_turn}' if shift else f'#{i} {rendered_turn}'


def render_example():
    window = '\n'.join(window_line(i, EXAMPLE_SHIFT, line) for i, line in enumerate(EXAMPLE_WINDOW, 1))
    return f'\n\nExample window (site {EXAMPLE_SITE}):\n{window}\nExample output:\n{jsonl(EXAMPLE_FACTS)}\n{EXAMPLE_NOTE}'


EXTRACT_USER = ('Site: {site}\nProduction calendar:\n{calendar}\nKnown identifiers at this site (most recent first): '
                '{known}\nDialogue before the window:\n{history}\n\nCurrent window:\n{window}')


def extraction_messages(site, calendar, known, history, window, example=True):
    system = EXTRACT_SYSTEM + (render_example() if example else '')
    user = EXTRACT_USER.format(site=site, calendar=calendar, known=', '.join(known) or 'none',
                               history=history or 'none', window=window)
    return [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]


MANAGE_SYSTEM = (
    'You maintain the long-term memory bank of an industrial agent. You get the facts just extracted from the latest '
    'window of the interaction log, numbered F1, F2, ..., and the existing memory entries related to them, with ids '
    'such as M12 (only entries that are still valid are shown). Give every new fact exactly one action:\n'
    '- ADD: the fact is new information; store it.\n'
    '- SUPERSEDE: the fact is a newer state of the same thing an entry describes -- the equipment or lot state '
    'changed, a newer reading replaces an older one, a hypothesis was confirmed or ruled out, an open item was done. '
    'The fact is stored and the old entry stops being valid (it is kept as history).\n'
    '- NOOP: the memory already holds this fact; store nothing.\n'
    'Separately, EXPIRE an entry only when the window states that it no longer holds and no new fact replaces it, '
    'e.g. a constraint that lapsed because its work order was closed. EXPIRE is rare: a newer fact about the same '
    'equipment does not make older entries invalid -- reports, readings, hypotheses and failed measures remain true '
    'as history.\n'
    'SUPERSEDE and EXPIRE may only name listed entries about the same equipment, lot or work order, and each entry '
    'at most once. Keep constraints and open items until the window says they changed or ended.\n'
    'Output one JSON object per line, then a last line ' + END_MARK + ':\n'
    '{{"fact": 1, "action": "ADD"}}\n{{"fact": 2, "action": "SUPERSEDE", "old": "M12"}}\n'
    '{{"fact": 3, "action": "NOOP", "old": "M4"}}\n{{"action": "EXPIRE", "old": "M7"}}\n' + END_MARK
)

MANAGE_USER = 'Window time: {start} to {end}\n\nNew facts:\n{facts}\n\nRelated memory entries:\n{memory}'


def render_fact(i, f):
    meta = ' | '.join(x for x in (f.time, f.shift, f.source) if x)
    ents = f'; entities: {", ".join(f.entities)}' if f.entities else ''
    return f'[F{i}] ({meta}{ents}) {f.content}'


def render_unit(u):
    meta = ' | '.join(x for x in (u.time.strftime('%Y-%m-%d %H:%M'), u.shift, u.source) if x)
    ents = f'; entities: {", ".join(u.entities)}' if u.entities else ''
    return f'[{u.uid}] ({meta}{ents}) {u.content}'


def management_messages(start, end, facts, units):
    user = MANAGE_USER.format(start=start, end=end, facts='\n'.join(render_fact(i, f) for i, f in enumerate(facts, 1)),
                              memory='\n'.join(render_unit(u) for u in units) or 'none')
    return [{'role': 'system', 'content': MANAGE_SYSTEM.format()}, {'role': 'user', 'content': user}]


CONTINUE = ('Your reply was cut off. Continue with the next line after the last complete one, in the same format, '
            'and end with ' + END_MARK + '.')
