"""Deterministically generate a synthetic manufacturing working-memory corpus.

Each scenario has 5 sessions, one per narrative phase (report -> hypothesis ->
failed intervention -> effective fix -> verification).

Phase 1 (hypothesis discussion) is a human-to-human collaboration session:
two engineer roles compare notes, mixing a dense fact report (high
information density) with small talk / acknowledgements (low density) and an
occasional system_tool status broadcast -- this is inherently a colleague
discussion, not something either party would ask an Agent about.

Phases 0, 2, 3, 4 are Agent diagnostic episodes: a human asks the industrial
Agent to check/confirm something (user_query), the Agent lays out a plan
(agent_think), issues several tool calls to rule out unrelated subsystems
(agent_tool_call, low density -- "normal / no anomaly"), until exactly one
tool call turns up the precise datum that actually explains the situation
(agent_tool_call, high density), and the Agent synthesizes a conclusion
(agent_response). This is the dataset's ReAct-style think -> act -> observe
chain: a gating mechanism must discard the routine "normal" tool returns
(and the Agent's own think turns, which are reasoning, not fact) while
keeping the one informative tool call and the final report. Phase 4 in
particular pairs a dense user report ("the issue seems resolved") with a
dense Agent tool confirmation ("system back to normal") -- two independently
dense pieces of evidence that a memory system must synthesize into a single
record.

Every turn is labeled with turn_type and is_low_density (bool) so gating
precision/recall can be scored directly. turn_type is one of:
user_query | agent_think | agent_tool_call | agent_response | human | tool_call
(human/tool_call remain for the phase-1 human-collaboration session).
agent_tool_call turns additionally carry a tool_name field identifying which
diagnostic query was invoked.

v0.2 additions:
- Production calendar. Every site runs a shift calendar (CALENDARS / SITE_CALENDAR,
  also written to production_calendar.json) and every session starts at a random
  time inside a random shift. The user_query of phases 0/2/3 always anchors the
  event to a shift ("本班..." / "上一班交接时说..."), carrying resolved_time and
  resolved_shift ground truth; the shift_temporal category asks which shift the
  event happened in, which is only answerable with the site's calendar.
- Unanswerable questions (answerable=false): an unseen look-alike entity, an
  attribute the dialogue never records, and a false premise.
- Cross-turn anaphora: in phases 2-4 the dense report sometimes says "这台设备/它/
  这一批" and names the entity only in a preceding low-density turn.
"""
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import tool_json

ROOT = Path(__file__).resolve().parents[1] / 'data' / 'generated'
random.seed(20260914)
rng = random.Random(20260914)

ROLES = [('op', 'operator'), ('eng', 'maintenance_engineer'), ('qa', 'quality_engineer'), ('proc', 'process_engineer')]
# v0.4: each domain has an owner role who reports the problem (session 1) and asks to close
# the task (session 5); v0.3 rotated roles by session index only, so e.g. a quality defect was
# always reported by the maintenance engineer and the gold named roles that never spoke.
DOMAIN_OWNER = {'maintenance': 'eng', 'quality': 'qa', 'process': 'proc', 'logistics': 'op'}
ROLE_NAMES = {'operator': '操作员', 'maintenance_engineer': '维修工程师', 'quality_engineer': '质量工程师',
              'process_engineer': '工艺工程师'}
DOMAINS = [('maintenance', '设备维护'), ('quality', '质量异常'), ('process', '工艺参数'), ('logistics', '物料追踪')]
CATS = ['single_hop_fact', 'temporal_order', 'state_tracking', 'failed_intervention', 'cross_role_dependency',
        'working_memory_snapshot', 'constraint_recall', 'superseded_fact', 'multi_hop_event', 'conditioned_parameter',
        'tool_evidence_recall', 'shift_temporal', 'unanswerable']
# difficulty follows the reasoning a category requires (it used to rotate per question index)
DIFFICULTY = {'single_hop_fact': 'easy', 'state_tracking': 'easy', 'constraint_recall': 'easy',
              'tool_evidence_recall': 'medium', 'failed_intervention': 'medium', 'superseded_fact': 'medium',
              'conditioned_parameter': 'medium', 'temporal_order': 'medium', 'shift_temporal': 'medium',
              'multi_hop_event': 'hard', 'cross_role_dependency': 'hard', 'working_memory_snapshot': 'hard',
              'unanswerable': 'hard'}
SHIFT_PHASES = [0, 2, 3]  # phases whose user_query anchors the event to a shift
UNANSWERABLE_KINDS = ['unseen_entity', 'unrecorded_attribute', 'false_premise']
MULTI_EVENT_CATS = {'temporal_order', 'failed_intervention', 'cross_role_dependency',
                     'working_memory_snapshot', 'constraint_recall', 'multi_hop_event',
                     'superseded_fact', 'conditioned_parameter'}
# build_answer() hardcodes which canon phase(s) each single-session category's answer text
# is drawn from (single_hop_fact always canon[0], state_tracking always canon[4]) -- evidence
# for those categories must point at that SAME phase's session, not a per-question rotating
# index, or the evidence turns won't actually support the answer text. superseded_fact and
# conditioned_parameter reference two phases (2 and 3) each, so they're treated as
# MULTI_EVENT_CATS instead (full-session evidence), matching the other multi-phase categories.
ANCHOR_PHASE = {'single_hop_fact': 0, 'state_tracking': 4}

# event_type / state vocabulary is domain-specific (ontology/event_types.yaml, ontology/state_types.yaml)
# because the 5 narrative phases mean different things per domain: an "alarm" only
# makes sense for maintenance; quality/logistics phases are containment/inspection acts.
EVENT_TYPES = {
    'maintenance': ['alarm', 'diagnosis_hypothesis', 'parameter_adjustment', 'component_replacement', 'verification'],
    'quality': ['quality_defect', 'diagnosis_hypothesis', 'containment', 'parameter_adjustment', 'verification'],
    'process': ['symptom_observed', 'diagnosis_hypothesis', 'parameter_adjustment', 'procedure_update', 'verification'],
    'logistics': ['inspection', 'diagnosis_hypothesis', 'containment', 'procedure_update', 'verification'],
}
STATES_BEFORE = {
    'maintenance': ['Normal', 'Alarmed', 'UnderInvestigation', 'Recurred', 'TemporarilyRecovered'],
    'quality': ['Released', 'Quarantined', 'UnderInvestigation', 'Recurred', 'UnderInvestigation'],
    'process': ['Normal', 'UnderInvestigation', 'UnderInvestigation', 'Recurred', 'TemporarilyRecovered'],
    'logistics': ['Normal', 'UnderInvestigation', 'UnderInvestigation', 'Recurred', 'TemporarilyRecovered'],
}
STATES_AFTER = {
    'maintenance': ['Alarmed', 'UnderInvestigation', 'Recurred', 'TemporarilyRecovered', 'Verified'],
    'quality': ['Quarantined', 'UnderInvestigation', 'Recurred', 'UnderInvestigation', 'Released'],
    'process': ['UnderInvestigation', 'UnderInvestigation', 'Recurred', 'TemporarilyRecovered', 'Verified'],
    'logistics': ['UnderInvestigation', 'UnderInvestigation', 'Recurred', 'TemporarilyRecovered', 'Verified'],
}
VERIF_STATUS = ['observed', 'hypothesis', 'failed', 'verified', 'verified']
AGENT_PHASES = {0, 2, 3, 4}  # first-report, failed-fix, effective-fix, verification -- all become Agent episodes
AGENT_PHASE_SESSIONS = [s for s in range(1, 6) if (s - 1) in AGENT_PHASES]  # 1-indexed session numbers -> [1, 3, 4, 5]
# the entity a scenario's narrative actually revolves around, per domain (used in question wording)
SUBJECT_OF_DOMAIN = {'maintenance': 'eq', 'quality': 'lot', 'process': 'eq', 'logistics': 'lot'}

# Site-level production calendars: (shift name, start HH:MM, length in hours). Shift
# boundaries differ between sites and never sit on midnight, so "上一班" can only be
# resolved with the site's calendar, not with clock-time common sense.
TZ = timezone(timedelta(hours=8))
CALENDARS = {
    'CAL_3SHIFT': [('早班', '07:30', 8), ('中班', '15:30', 8), ('夜班', '23:30', 8)],
    'CAL_2SHIFT': [('白班', '08:00', 12), ('夜班', '20:00', 12)],
}
SITE_CALENDAR = {'SITE_01': 'CAL_3SHIFT', 'SITE_02': 'CAL_3SHIFT', 'SITE_03': 'CAL_3SHIFT',
                 'SITE_04': 'CAL_2SHIFT', 'SITE_05': 'CAL_2SHIFT'}
NEXT_DAY = '次日'
SHIFT_LABEL = '{shift_id}（{start}–{end}）'

# canonical (precise) fact per domain/phase -- used for memory_units + answers + the
# Agent's final agent_response turn (the episode's conclusion must land on the same
# canonical wording the rest of the pipeline already keys off of).
CANON = {
    # v0.4: maintenance wording is filled per fault family (MAINT_FAMILY: {symptom}, {measure})
    # so the alarm, the failed measure and the readings all concern the same failing component.
    'maintenance': [
        '{eq}出现设备报警{alarm}（{symptom}）。',
        '初步检查后怀疑部件{comp}，但尚未确认。',
        '{measure}后短暂恢复，随后再次出现问题。',
        '更换{comp}后设备恢复，等待连续运行验证。',
        '{eq}连续运行验证通过，任务{task}可以关闭。',
    ],
    # v0.4: a quarantined lot is reworked, not "fixed" by a machine setting, and the acceptance
    # threshold is never relaxed; the root cause is a drifting preheat sensor on the machine.
    'quality': [
        '{lot}在{eq}产出的焊点空洞率超限，质量工程师发起隔离。',
        '复核发现{eq}预热段实际温度偏低，怀疑{comp}读数漂移，根因尚待确认。',
        '临时上调{eq}预热设定后{lot}返工短暂改善，但抽检空洞率再次超限。',
        '更换{comp}并重新校准预热段，{lot}返工复检合格，进入待放行状态。',
        '{lot}放行前复检全部合格，任务{task}可以关闭。',
    ],
    # v0.4: every conclusion is backed by the tool that measures it (yield, first-article
    # inspection items, Cpk) instead of reusing yield figures for all of them.
    'process': [
        '{eq}良率异常下降，低于目标85%。',
        '初步怀疑{eq}的预热参数与材料{lot}不匹配。',
        '{eq}第一次上调预热温度后良率短暂恢复，随后回落。',
        '{eq}针对材料{lot}更新参数配方并完成首件确认，等待稳定性验证。',
        '{eq}稳定性验证与过程能力复核通过，任务{task}可以关闭。',
    ],
    # v0.4: one consistent count (12 cartons) and the final check is the inventory count the
    # previous phase said it was waiting for.
    'logistics': [
        '物料批次{lot}在{eq}上线前找不到完整追溯记录。',
        '核对后怀疑{comp}上的标签与批次对应关系错误。',
        '{lot}临时补录标签没能解决问题，仍有一箱无法匹配批次关联。',
        '重新扫描并更正{lot}的批次关联，等待盘点确认。',
        '盘点确认{lot}全部12箱与批次关联匹配，任务{task}可以关闭。',
    ],
}

# v0.4 maintenance fault families, keyed by the scenario's component: which quantity the
# fault shows up in, the failed first measure, and the decisive readings of phases 2-4.
# Sensor faults are exposed by a hand-held cross-check; mechanical faults by a real change.
MAINT_FAMILY = {
    '温度传感器': {
        'tool': 'temperature_query', 'symptom': '主轴温度超限', 'measure': '降低{eq}的主轴转速',
        2: 'temperature_query返回：降速后{eq}主轴温度读数一度回落，随后再次升至81°C，超过报警上限75°C，判定本次调整未生效，检测时间{ts}。',
        3: 'temperature_query返回：更换{comp}后{eq}主轴温度读数稳定在58°C，与手持测温仪实测57°C一致，检测时间{ts}。',
        4: 'temperature_query返回：{eq}连续运行72小时主轴温度读数稳定在56-60°C，未超过上限75°C，验证时间{ts}。'},
    '冷却液传感器': {
        'tool': 'coolant_level_query', 'symptom': '冷却液液位低', 'measure': '给{eq}补充冷却液',
        2: 'coolant_level_query返回：补液后{eq}冷却液液位读数一度恢复，随后再次降至32%，低于下限60%，而液位视窗显示液位正常，判定本次处置未生效，检测时间{ts}。',
        3: 'coolant_level_query返回：更换{comp}后{eq}冷却液液位读数为88%，与液位视窗一致，检测时间{ts}。',
        4: 'coolant_level_query返回：{eq}连续运行72小时冷却液液位读数稳定在85%-90%，无低液位报警，验证时间{ts}。'},
    '振动传感器': {
        'tool': 'vibration_query', 'symptom': '主轴振动超限', 'measure': '重新紧固{eq}振动传感器的安装螺栓',
        2: 'vibration_query返回：紧固后{eq}振动读数一度正常，随后再次跳变至4.1mm/s，超过报警阈值2.5mm/s，而手持测振仪实测仅1.3mm/s，判定本次处置未生效，检测时间{ts}。',
        3: 'vibration_query返回：更换{comp}后{eq}振动读数1.2mm/s，与手持测振仪一致，低于报警阈值2.5mm/s，检测时间{ts}。',
        4: 'vibration_query返回：{eq}连续运行72小时振动读数稳定在1.1-1.3mm/s，未超过阈值2.5mm/s，验证时间{ts}。'},
    '主轴轴承': {
        'tool': 'vibration_query', 'symptom': '主轴振动超限', 'measure': '调整{eq}主轴轴承的预紧力',
        2: 'vibration_query返回：调整预紧力后{eq}振动一度降至2.1mm/s，随后回升至3.8mm/s，超过报警阈值2.5mm/s，判定本次调整未生效，检测时间{ts}。',
        3: 'vibration_query返回：更换{comp}后{eq}振动降至1.1mm/s，低于报警阈值2.5mm/s，检测时间{ts}。',
        4: 'vibration_query返回：{eq}连续运行72小时振动稳定在1.0-1.3mm/s，未超过阈值2.5mm/s，验证时间{ts}。'},
    '传动皮带': {
        'tool': 'vibration_query', 'symptom': '振动超限并伴有异响', 'measure': '调整{eq}传动皮带的张紧度',
        2: 'vibration_query返回：张紧后{eq}振动一度正常，随后回升至3.5mm/s并伴有皮带打滑，超过报警阈值2.5mm/s，判定本次调整未生效，检测时间{ts}。',
        3: 'vibration_query返回：更换{comp}后{eq}振动降至1.2mm/s，异响消失，检测时间{ts}。',
        4: 'vibration_query返回：{eq}连续运行72小时振动稳定在1.1-1.4mm/s，未超过阈值2.5mm/s，验证时间{ts}。'},
}

# dialogue surface paraphrases per domain/phase (index 0 == canonical wording).
# Reused both as the phase-1 human dense report and as the basis for an Agent
# episode's opening user_query (phase 1 discussion excluded). Every phase across
# every domain carries at least one {eq}/{lot}/{comp} placeholder -- phase2/phase3
# used to be entity-free in quality/process/logistics (and phase2 in maintenance),
# which meant those phases' dialogue text AND canonical answer text were byte-for-byte
# identical across every scenario in that domain (25 scenarios sharing one sentence).
# v0.4: phases 0/2/3 are prefixed with a shift anchor ("上一班交接时说，..."), so no variant
# says "刚才/刚" (it contradicted a previous-shift anchor); phase-4 variants propose closing
# the task instead of stating it is already closed (the Agent then verifies before closing).
PARAPHRASE = {
    'maintenance': [
        ['{eq}出现设备报警{alarm}（{symptom}）。', '{eq}跳了一次警，代码是{alarm}，{symptom}。', '提醒一下，{eq}触发了报警{alarm}，提示{symptom}。', '{eq}这边报警了，代码{alarm}，显示{symptom}，我先记一下。'],
        ['初步检查后怀疑部件{comp}，但尚未确认。', '看了一下，可能是{comp}的问题，还需要再核实。', '初步判断{comp}有异常，先记录成疑点。', '怀疑是{comp}导致的，不过还不能下结论。'],
        ['{measure}后短暂恢复，随后再次出现问题。', '试着{measure}，起初看着好了，但没多久又犯了。', '{measure}这个办法只维持了很短时间，问题又复现了。', '{measure}之后一度正常，可没过多久问题又来了。'],
        ['更换{comp}后设备恢复，等待连续运行验证。', '把{comp}换掉之后，现在看着稳定了，再观察一阵子。', '换了新的{comp}，暂时没再报警，等连续运行验证。', '{comp}已经更换完成，设备恢复运行，等验证。'],
        ['{eq}连续运行验证通过，任务{task}可以关闭。', '验证跑完了，{eq}这段时间很稳定，{task}可以结案了吧。', '{eq}这几天都没再出问题，{task}这边可以收尾了。', '验证结果没问题，{task}可以关闭了。'],
    ],
    'quality': [
        ['{lot}在{eq}产出的焊点空洞率超限，质量工程师发起隔离。', '{lot}这批在{eq}上做出来的东西空洞率超标了，已发起隔离。', '检出{lot}焊点空洞率不合格，已经发起隔离。', '{eq}产出的{lot}空洞率超限，已经发起隔离。'],
        ['复核发现{eq}预热段实际温度偏低，怀疑{comp}读数漂移，根因尚待确认。', '复查了一下，{eq}预热段实际温度偏低，可能是{comp}读数漂了，根因还没定。', '怀疑{comp}读数不准，{eq}预热段实际偏低，待确认。', '初步看是{comp}漂移导致{eq}预热段实际温度偏低，具体原因待查。'],
        ['临时上调{eq}预热设定后{lot}返工短暂改善，但抽检空洞率再次超限。', '先把{eq}预热设定往上调了一点，{lot}返工一开始还行，抽检又超了。', '{eq}临时上调预热设定只管用了一阵，{lot}返工抽检又不合格。', '{eq}上调预热设定后{lot}返工短期正常，可抽检空洞率再次超标。'],
        ['更换{comp}并重新校准预热段，{lot}返工复检合格，进入待放行状态。', '把{comp}换了，预热段也重新校准了，{lot}返工复检合格，现在等放行。', '{comp}已更换、预热段已校准，{lot}返工复检通过，待放行。', '更换{comp}并校准预热段后，{lot}返工复检合格，进入待放行。'],
        ['{lot}放行前复检全部合格，任务{task}可以关闭。', '{lot}放行前的复检都过了，{task}可以关了吧。', '复检结果全部合格，{lot}这边{task}可以结案了。', '{lot}这批放行前复检都通过了，任务{task}可以关闭。'],
    ],
    'process': [
        ['{eq}良率异常下降，低于目标85%。', '{eq}这边良率掉了，低于85%的目标。', '发现{eq}良率在下降，已经低于目标85%。', '{eq}良率异常下滑，跌破85%目标，先记录。'],
        ['初步怀疑{eq}的预热参数与材料{lot}不匹配。', '怀疑是{eq}的预热参数跟{lot}这批材料不匹配。', '工艺这边觉得可能是{eq}的参数和{lot}材料不搭。', '初步判断{eq}预热参数与{lot}材料不匹配，待确认。'],
        ['{eq}第一次上调预热温度后良率短暂恢复，随后回落。', '{eq}第一次把预热温度调高之后良率好了一下，接着又掉下去了。', '{eq}上调预热温度后良率短暂回升，没多久又回落。', '{eq}第一次上调预热温度只带来短暂改善，良率又降了。'],
        ['{eq}针对材料{lot}更新参数配方并完成首件确认，等待稳定性验证。', '{eq}针对{lot}材料把配方更新了，首件也确认过了，等稳定性验证。', '{eq}按{lot}材料更新了参数配方，首件确认通过，剩下稳定性验证。', '{eq}针对{lot}的参数配方已更新，首件确认完成，等验证结果。'],
        ['{eq}稳定性验证与过程能力复核通过，任务{task}可以关闭。', '{eq}稳定性验证和过程能力复核都过了，{task}可以关闭了吧。', '复核结果显示{eq}过程能力达标、运行稳定，{task}可以结案。', '{eq}稳定性验证通过，过程能力也复核过了，任务{task}可以关闭。'],
    ],
    'logistics': [
        ['物料批次{lot}在{eq}上线前找不到完整追溯记录。', '{lot}这批在{eq}上线前追溯记录不全。', '上线前发现{lot}追溯记录缺失，在{eq}这边。', '{eq}上线前{lot}的追溯记录找不全。'],
        ['核对后怀疑{comp}上的标签与批次对应关系错误。', '核对完了，怀疑{comp}上的标签跟批次对应错了。', '怀疑{comp}的标签贴错了批次，待核实。', '初步判断{comp}上的标签和批次对不上，正在核实。'],
        ['{lot}临时补录标签没能解决问题，仍有一箱无法匹配批次关联。', '补录了{lot}的标签，但没解决问题，还有一箱对不上批次关联。', '{lot}临时补录标签这个办法没解决问题，还有一箱匹配不了。', '{lot}补录标签没能解决，仍剩一箱匹配不上。'],
        ['重新扫描并更正{lot}的批次关联，等待盘点确认。', '重新扫了一遍，把{lot}的批次关联改过来了，等盘点确认。', '{lot}重新扫描更正了批次关联，剩下盘点确认。', '{lot}批次关联已经改正，重新扫描完，等盘点。'],
        ['盘点确认{lot}全部12箱与批次关联匹配，任务{task}可以关闭。', '{lot}盘点做完了，12箱都对上了，{task}可以关闭了吧。', '盘点确认{lot}这12箱关联都正确，{task}可以结案。', '{lot}全部12箱盘点匹配，任务{task}可以关闭。'],
    ],
}

# --- Agent diagnostic-episode vocabulary (phases 0, 2, 3, 4) ---------------

# per-domain tool_name vocabulary an Agent can invoke via agent_tool_call
TOOL_NAMES = {
    'maintenance': ['temperature_query', 'vibration_query', 'coolant_level_query', 'lubrication_pressure_query', 'alarm_log_query'],
    'quality': ['void_rate_query', 'preheat_temp_query', 'material_batch_query', 'inspection_history_query',
                'solder_paste_query', 'conveyor_speed_query', 'flux_query'],
    'process': ['yield_query', 'parameter_window_query', 'material_match_query', 'first_article_query',
                'process_capability_query', 'equipment_status_query', 'maintenance_record_query', 'environment_query'],
    'logistics': ['traceability_query', 'label_scan_query', 'inventory_query', 'batch_association_query',
                  'wms_location_query', 'transport_record_query', 'receiving_record_query'],
}
# v0.4: tools that measure the scenario's open problem never appear as a routine "normal"
# check -- v0.3 ruled out e.g. "batch association correct" right before finding an unmatched
# carton. Maintenance also never rules out its own fault quantity (MAINT_FAMILY tool) or
# lists "no new alarms" while an alarm is open.
PROBLEM_TOOLS = {
    'maintenance': {'alarm_log_query'},
    'quality': {'void_rate_query', 'preheat_temp_query', 'inspection_history_query'},
    'process': {'yield_query', 'parameter_window_query', 'material_match_query', 'first_article_query',
                'process_capability_query'},
    'logistics': {'traceability_query', 'label_scan_query', 'inventory_query', 'batch_association_query'},
}
TOOL_LABEL = {
    'temperature_query': '温度', 'vibration_query': '振动', 'coolant_level_query': '冷却液液位',
    'lubrication_pressure_query': '润滑压力', 'alarm_log_query': '报警日志',
    'void_rate_query': '空洞率', 'preheat_temp_query': '预热温度', 'material_batch_query': '物料批次信息',
    'inspection_history_query': '检验历史', 'solder_paste_query': '锡膏状态', 'conveyor_speed_query': '链速',
    'flux_query': '助焊剂比重',
    'yield_query': '良率', 'parameter_window_query': '工艺参数窗口', 'material_match_query': '材料匹配',
    'first_article_query': '首件检验', 'process_capability_query': '过程能力', 'equipment_status_query': '设备运行状态',
    'maintenance_record_query': '保养记录', 'environment_query': '车间温湿度',
    'traceability_query': '追溯记录', 'label_scan_query': '标签扫描', 'inventory_query': '库存盘点',
    'batch_association_query': '批次关联', 'wms_location_query': '库位信息', 'transport_record_query': '运输交接记录',
    'receiving_record_query': '收货记录',
}
ROUTINE_VALUE_RANGE = {
    'temperature_query': (55, 68), 'vibration_query': (0.8, 1.8), 'coolant_level_query': (70, 95),
    'lubrication_pressure_query': (0.35, 0.55), 'conveyor_speed_query': (1.0, 1.4), 'flux_query': (0.80, 0.84),
    'environment_query': (22, 26),
}

# routine ("no anomaly") returns -- low density, one template per tool_name so the
# gate has to distinguish tool identity, not just guess from boilerplate wording.
AGENT_ROUTINE = {
    'maintenance': {
        'temperature_query': 'temperature_query返回：{eq}主轴温度{v}°C，处于正常区间（≤75°C）。',
        'vibration_query': 'vibration_query返回：{eq}振动值{v}mm/s，低于报警阈值2.5mm/s，正常。',
        'coolant_level_query': 'coolant_level_query返回：{eq}冷却液液位{v}%，处于正常范围（60%-100%）。',
        'lubrication_pressure_query': 'lubrication_pressure_query返回：{eq}润滑压力{v}MPa，处于正常范围（0.3-0.6MPa）。',
    },
    'quality': {
        'material_batch_query': 'material_batch_query返回：{lot}物料批次信息核验一致，无异常标记。',
        'solder_paste_query': 'solder_paste_query返回：{eq}在用锡膏批次在有效期内，回温记录正常。',
        'conveyor_speed_query': 'conveyor_speed_query返回：{eq}链速{v}m/min，处于设定范围内（1.0-1.4m/min）。',
        'flux_query': 'flux_query返回：{eq}助焊剂比重{v}，处于规格范围内（0.80-0.84）。',
    },
    'process': {
        'equipment_status_query': 'equipment_status_query返回：{eq}运行状态正常，近24小时无停机记录。',
        'maintenance_record_query': 'maintenance_record_query返回：{eq}近期保养按计划完成，无遗留项。',
        'environment_query': 'environment_query返回：{eq}所在车间温度{v}°C，温湿度处于规定范围内。',
    },
    'logistics': {
        'wms_location_query': 'wms_location_query返回：{lot}库位信息与WMS记录一致。',
        'transport_record_query': 'transport_record_query返回：{lot}运输交接记录完整，无破损登记。',
        'receiving_record_query': 'receiving_record_query返回：{lot}收货箱数与送货单一致，共12箱。',
    },
}

# the single high-density tool call per phase -- carries the precise datum
# (tool_name, template) that the Agent's final response is actually grounded in.
# Maintenance phases 2-4 come from MAINT_FAMILY. v0.4: {ts} is when the quoted measurement
# was taken (the reported occurrence, or shortly before the verification session), never
# after the user already reported its result.
AGENT_INFORMATIVE = {
    'maintenance': {
        0: ('alarm_log_query', 'alarm_log_query返回：{eq}触发报警代码{alarm}（{symptom}），触发时间{ts}，当前仍处于报警状态。'),
    },
    'quality': {
        0: ('void_rate_query', 'void_rate_query返回：{lot}空洞率检测值4.8%，超过上限3.0%，判定不合格，检测时间{ts}。'),
        2: ('void_rate_query', 'void_rate_query返回：上调预热设定后{lot}返工抽检空洞率回升至4.2%，仍超过上限3.0%，判定临时调整无效，检测时间{ts}。'),
        3: ('void_rate_query', 'void_rate_query返回：更换{comp}并校准预热段后，{lot}返工复检空洞率1.8%，低于上限3.0%，合格，检测时间{ts}。'),
        4: ('inspection_history_query', 'inspection_history_query返回：{lot}放行前3次复检空洞率分别为1.6%、1.9%、1.7%，均低于上限3.0%，验证通过，检测时间{ts}。'),
    },
    'process': {
        0: ('yield_query', 'yield_query返回：{eq}当前良率68.5%，低于目标85%，判定异常，检测时间{ts}。'),
        2: ('yield_query', 'yield_query返回：上调预热温度后{eq}良率一度回升至82%，随后回落至70.2%，判定本次调整未生效，检测时间{ts}。'),
        3: ('first_article_query', 'first_article_query返回：更新配方后{eq}首件尺寸、外观检验项目全部合格，首件确认通过，检测时间{ts}。'),
        4: ('process_capability_query', 'process_capability_query返回：{eq}自配方更新以来连续5批关键尺寸Cpk为1.52，高于要求1.33，控制图无失控点，各批良率均高于85%，稳定性验证通过，检测时间{ts}。'),
    },
    'logistics': {
        0: ('traceability_query', 'traceability_query返回：{lot}追溯记录缺失2个关键节点（入库扫描、批次关联），无法形成完整追溯链，检测时间{ts}。'),
        2: ('inventory_query', 'inventory_query返回：补录标签后盘点显示{lot}共12箱中仍有1箱无法匹配批次关联，判定补录未解决问题，检测时间{ts}。'),
        3: ('batch_association_query', 'batch_association_query返回：重新扫描后{lot}批次关联已更正，12箱中已关联12箱，检测时间{ts}。'),
        4: ('inventory_query', 'inventory_query返回：盘点确认{lot}全部12箱与批次关联匹配（含此前无法匹配的1箱），检测时间{ts}。'),
    },
}

# v0.4: no "what is the cause?" suffix -- the Agent's conclusion reports the finding, and a
# question it never answers read as incoherent
# v0.5: no '现在的数据' -- the decisive record is the measurement taken when the event happened
AGENT_QUERY_SUFFIX = ['，你帮我确认一下具体情况。', '，麻烦核实一下。', '，帮我查一下相关记录。', '，帮我看一下。']
AGENT_ACK_SHORT = ['收到，我这就查一下。', '好的，稍等我看一下相关数据。', '明白，马上帮你核实。', '好，我先排查一下。']
AGENT_THINK_MID = ['刚才检查的几项都正常，我再确认一下{last_label}。', '常规项暂时没发现问题，再看一下{last_label}这项。', '目前排查的部分都在正常范围，继续确认{last_label}。']
AGENT_THINK_MID_ONE = ['这项正常，再确认一下{last_label}。', '常规项没发现问题，再看一下{last_label}这项。']  # v0.5: one routine check
AGENT_FILLER_THINK = ['这项正常，继续看下一项。', '没问题，接着排查下一个点。', '这个排除了，继续往下查。']
# v0.4: question/answer pairs asked *after* the conclusion (v0.3 drew them independently,
# e.g. "大概还要多久？" -> "已经在查了" after the Agent had already concluded)
# v0.5: no "no floor action needed" while an alarm is open, and no claim of a write-back the Agent never made
AGENT_FOLLOWUP = [('后面还需要注意什么？', '我会继续关注相关数据，有变化第一时间同步。'),
                  ('这个结论要记到工单里吗？', '要的，我会把这个结论记到{task}的处理记录里。'),
                  ('有新情况的话怎么通知？', '有新的读数我会第一时间在这里同步。')]  # v0.5: no overlap with question templates

# v0.4: phase-1 status broadcasts no longer claim the equipment is "normal" while the
# problem is still open
# v0.5: pushed notifications (nobody queried them), labelled as such
TOOL_ROUTINE = [
    '系统推送：{eq}的点检计划按期执行中。',
    '系统推送：任务{task}已在MES登记，状态为处理中。',
    '系统推送：{eq}能耗数据已同步至EMS。',
    '系统推送：{lot}相关记录本班无新增。',
    '系统推送：本区域其他设备巡检状态正常。',
    '系统推送：本班暂无新增告警记录。',
]

# v0.5: openers that start the conversation (v0.3 had replies such as "在的，您说" to a call nobody made)
SMALLTALK_OPEN = ['正好碰到你，说一下情况。', '早，一切还好吧？', '刚接班，有什么要交代的吗？', '有空吗，对一下进展。']
# acknowledgements in the colleague discussion: no claim that something was already arranged
COLLAB_ACK_POOL = ['好，我知道了。', '收到，保持同步。', '明白，会持续关注。', '好，有情况再说。']

# (text, resolution_kind, entity_key) -- resolution_kind in
# {'time_prev' (previous session), 'shift_prev' (previous shift), 'time_recent', 'entity'}
CONTEXT_TURNS = [
    ('上一班交接时提到的情况，现在{eq}怎么样了？', 'shift_prev', 'eq'),
    ('刚才说的那个问题，现在处理得怎么样了？', 'time_recent', None),
    ('这一批还有别的问题吗？', 'entity', 'lot'),
    ('这台设备现在稳定了吗？', 'entity', 'eq'),
    ('它现在的状态正常吗？', 'entity', 'eq'),
    ('这个批次后面还需要跟进吗？', 'entity', 'lot'),
]

# same resolution_kind vocabulary as CONTEXT_TURNS, but sized/worded to prepend onto an
# Agent episode's user_query ("上一次报的那个问题呢，{dense report}...") so relative-time
# / vague-referent coverage lands in the Agent interactions, not just the collab session.
AGENT_QUERY_PREFIX = [
    ('上一次报的那个问题呢，', 'time_prev', 'eq'),
    ('刚才说的那个情况，', 'time_recent', None),
    ('它现在怎么样了，', 'entity', 'eq'),
    ('这一批后续没别的问题吧，', 'entity', 'lot'),
    ('这台设备现在稳定了吗，', 'entity', 'eq'),
    ('这个批次还需要跟进吗，', 'entity', 'lot'),
]

# agent_think turns that anaphorically reference an earlier report/entity instead of
# restating it, with the same resolved_time/resolved_entities ground-truth convention.
AGENT_THINK_CONTEXT = [
    ('结合刚才提到的情况，我再确认一下{eq}最新的数据。', 'entity', 'eq'),
    ('刚才那一批的问题还没完全排除，我先查一下相关记录。', 'entity', 'lot'),
    ('上一班交接时提到的情况，我一并核实一下。', 'shift_prev', 'eq'),
    ('结合刚才那次异常，我看一下它现在是不是还有问题。', 'time_recent', 'eq'),
]

# shift anchor opening the user_query of phases 0/2/3: when did the reported event
# happen, relative to the shift the session runs in ('cur' = this shift, 'prev' = the
# shift before it, looked up in the site's production calendar).
SHIFT_OCC_PREFIX = {
    'cur': ['本班接班以后，', '这个班早些时候，', '今天这个班上，'],  # v0.4: no '开班后不久' (occurrence may be hours in)
    'prev': ['上一班交接时说，', '接班时听上一班讲，', '上一班值班期间，'],
}

# cross-turn anaphora: a low-density opener names the subject, then the dense report
# refers to it only by an anaphor, so the report is uninterpretable on its own.
ANAPHOR_OPENER = ['{subject}那边我刚去现场看过。', '关于{subject}，我这边有个更新。', '我这边是{subject}的事。']  # v0.5: no opener that reads as a request
ANAPHOR = {'eq': ['这台设备', '它'], 'lot': ['这一批', '这个批次']}

SHIFT_QUESTION_VARIANTS = {
    0: ['{subject}最初的问题是在哪个班次发生的？', '{subject}的异常最早出现在哪一个班次？', '{subject}的问题第一次出现是哪天哪个班？'],
    # v0.5: phase 2 asks when the first measure was found not to work (it never "held" in every domain);
    # phase 3 asks when the reading showing the improvement was taken -- "first check" also matched the
    # routine checks, "确认起效" the final verification
    2: ['{subject}发现第一次处置没有奏效是在哪个班次？', '{subject}第一次处置被证实无效，是哪天哪个班测得的？', '{subject}那次没奏效的处置，其无效结果是在哪个班次测得的？'],
    3: ['{subject}换用新措施后，显示情况好转的那次检测是在哪个班次测得的？', '显示{subject}在新措施后恢复正常的那次读数，是哪天哪个班测得的？',
        '{subject}的有效措施实施后，证明好转的那次检测是在哪个班次？'],
}

# unanswerable questions -- the history never contains the answer
UNSEEN_ENTITY_CATS = ['single_hop_fact', 'state_tracking', 'failed_intervention']  # templates reused with a look-alike ID
UNRECORDED_ATTRIBUTE_QUESTIONS = ['{subject}相关物料或备件是哪家供应商提供的？', '{subject}这次问题处理一共花了多少费用？',
                                  '{subject}这次问题是哪家客户投诉的？']
# v0.5: events that never happened ("验证后复发了几次" was answerable as zero, and "第三次调整" could be
# counted to the fix)
FALSE_PREMISE_QUESTIONS = ['{subject}第二次失败的处置措施具体是什么？', '{subject}第二次停线整改是由谁负责的？',
                           '{subject}的第二个故障部件是在哪个班次更换的？']
NO_INFO_ANSWER = '历史记录中没有相关信息，无法回答。'
# measures taken on the subject before the failed fix (conditioned_parameter gold), per domain
CONTAINMENT = {'quality': '发起隔离（控制措施）；'}

# Task-level constraints and the open item are stated in the dialogue (phase 3 once the
# fix awaits verification, phase 4 after closing), so constraint_recall and
# working_memory_snapshot are answerable from the history. In v0.1 they existed only in
# working_memory.jsonl, with raw EQ_xxxx ids the dialogue never shows.
CONSTRAINT_TEMPLATES = ['{subject}验证完成前不得关闭任务{task}', '不得再次采用已失败的调整方案']
CONSTRAINT_JOIN = '；'
CONSTRAINT_NOTE = '提醒一下两条约束：{constraints}。'
# v0.5: a follow-up after closing (archiving the records is part of closing, so an open archiving item
# contradicted "可以关闭")
PENDING_TEMPLATE = '把这次的故障模式补充进{subject}的点检SOP'  # v0.5: a lessons-learned item, not blocking closure
PENDING_NOTE = '还有一项不影响结案的待办：{pending}，这个先挂着。'

EXTRA_QA = [
    ('现在需要停机处理吗？', '暂时不用，先观察。'),
    ('要不要先上报？', '先记录，看后续发展再说。'),
    ('需要通知其他班组吗？', '先不用，我们这边跟进。'),
]

ACK_POOL = ['好的，我这就去办。', '收到，已经安排。', '明白，会持续关注。', '好，继续跟进。', '收到，保持同步。',
            '好，我知道了。', '行，交给我吧。', '收到，安排上了。']
# v0.4: '谢谢提醒' only answers an actual reminder (the constraints / open item note)
NOTE_ACK_POOL = ['了解，谢谢提醒。', '收到，我会注意。', '好，我记下了。']
SMALLTALK_CLOSE = ['好，先这样，有情况再同步。', '行，我这边继续盯着。', '嗯，保持关注。', '先这样，有更新再说。', '好，辛苦了。']

# domain-neutral wording keyed by {subject} (the entity the narrative revolves
# around per domain -- equipment for maintenance/process, material lot for
# quality/logistics; see SUBJECT_OF_DOMAIN) so no template presumes a
# maintenance-only concept like "alarm code" or "component replacement".
QUESTION_VARIANTS = {
    'single_hop_fact': ['{subject}最早被记录的问题是什么？', '{subject}最初出现的异常是什么？', '关于{subject}，最早记录的情况是什么？',
                         '{subject}第一次被记录的问题是什么？', '能说说{subject}最初的异常记录吗？', '{subject}最早出现的问题是什么？'],
    # v0.4: every wording names the two measures being ordered (the failed one and the effective
    # one); "哪一步在前，哪一步在后" left open which of several steps to compare
    'temporal_order': ['{subject}那次没有奏效的处置和后来生效的处置，哪一项先发生？', '在{subject}的处理过程中，是先出现失败的那次处置，还是先出现后来生效的那次？',
                        '{subject}失败的处置措施和最终生效的措施，先后顺序是什么？', '请说明{subject}失败的那次干预与生效的那次干预的时间先后。',
                        '{subject}的处置过程中，失败的措施和生效的措施哪个在前？', '关于{subject}，没奏效的措施和生效的措施分别是先做还是后做的？'],
    # v0.5: asks for the last verified status of the subject itself ("最新状态" was read as including the open
    # follow-up item of the task)
    'state_tracking': ['截至{as_of}，{subject}最后一次验证的结论是什么？', '{subject}最近一次验证给出的结论是什么？', '到{as_of}为止，{subject}最后一次验证的结果如何？',
                        '{subject}最新一次验证的结论是什么？', '截至目前，{subject}最后一次验证的结论是什么？', '{subject}最后一次验证时处于什么状态？'],  # v0.5: 验证, not any routine check
    'failed_intervention': ['{subject}哪项干预曾失败并导致问题复发？', '{subject}处理过程中，哪个措施没有生效？', '针对{subject}，之前尝试过但失败的方法是什么？',
                             '{subject}的哪次调整后来证明是无效的？', '在解决{subject}问题时，哪一步失败了？', '{subject}曾经失败的干预措施是什么？'],
    # v0.3: role-neutral -- v0.2 wordings presumed an "operator -> engineer" hand-off the
    # dialogue never shows (every scenario's first report and final confirmation come
    # from the maintenance engineer, see ROLES by session index)
    # v0.4: every wording asks only for the two ends (first report, final confirmation); "分别是
    # 哪些角色参与的" invited listing every intermediate speaker, which the gold does not
    # v0.5: asks for the two roles only, and the gold names only the roles
    'cross_role_dependency': ['{subject}的问题最早是由哪个角色报告的，最后又是由哪个角色提请确认结案的？',
                               '关于{subject}，最初报告问题的是什么角色，最后提请确认的又是什么角色？',
                               '{subject}这次事件中，最先报告问题的是什么角色，最后提请结案确认的是什么角色？',
                               '针对{subject}，最初报告问题的人和最后提请确认的人各是什么角色？',
                               '{subject}的处理中，报告问题和提请结案这两端分别是什么角色？',
                               '关于{subject}，问题是哪个角色先提出的，最后又是哪个角色提请确认的？'],
    # v0.3: the snapshot is the task's latest state plus its open item (the gold used to list
    # the superseded "awaiting verification" fact and the already-satisfied constraints too)
    'working_memory_snapshot': ['截至{as_of}，任务{task}的最新状态和待办是什么？', '到{as_of}为止，{task}的工作记忆里有哪些内容（最新状态与待办）？',
                                 '{task}目前的最新状态和未完成事项有哪些？', '截至{as_of}，{task}的最新状态如何，还有哪些未完成事项？',
                                 '{task}当前的工作记忆快照包含什么（最新状态与待办）？', '到目前为止，{task}的最新状态和遗留待办是什么？'],
    # v0.3: asked about the constraints registered during the task -- by as_of the task is
    # verified and closable, so "which constraints apply *now*" had no clean answer
    # v0.5: only asks for the registered constraints ("关闭前必须满足哪些条件" does not cover "不得重复失败方案")
    'constraint_recall': ['处理任务{task}期间登记过哪些约束条件？', '{task}在处理过程中被要求遵守哪些约束？', '针对{task}，现场明确提出过哪些约束？',
                           '{task}的处置过程中规定了哪些不能违反的约束？', '{task}处理期间有人提醒过哪些约束？', '{task}处理期间登记了哪些约束？'],
    # v0.3: asked about the superseded *measure* (MEM_04 supersedes MEM_03), which is what the
    # gold names -- v0.2 asked about a "hypothesis", and the only hypothesis in the dialogue
    # (phase 1) turns out to be right
    # v0.4: asks about the *failed* measure -- "最初的措施" is the quarantine in quality scenarios
    'superseded_fact': ['{subject}没有奏效的那项处置措施后来被什么措施取代？', '关于{subject}，失败的处理办法后来改成了什么？',
                         '{subject}失败的做法和后来生效的做法分别是什么？', '{subject}哪项失败的处置后来被新的措施替代？',
                         '关于{subject}，后来生效的措施替代了之前哪项失败的措施？', '{subject}的处置方案在第一次失败后发生了什么变化？'],
    # v0.4: bounded at the verification, so the open archiving item is out of scope
    'multi_hop_event': ['请按时间说明{subject}从问题出现到验证通过的完整过程。', '{subject}这次问题从发现到验证通过经历了哪些阶段？',
                         '能按顺序讲一下{subject}从发现问题到验证通过的处理过程吗？', '{subject}从问题出现到任务可以关闭，经过了哪些步骤？',
                         '请梳理{subject}问题从出现到验证通过的完整处理链条。', '{subject}这次事件从发现到验证通过的来龙去脉是怎样的？'],
    # v0.5: asks for the measures in order, including the one that failed (the gold marks which worked)
    'conditioned_parameter': ['针对{subject}先后采取过哪些措施（包括没奏效的）？', '针对{subject}先后做过哪些处置，各自效果如何？',
                               '关于{subject}，先后尝试过哪些措施？', '针对{subject}先后采取了哪些措施，哪项之后验证通过了？',
                               '{subject}的问题处理中先后经历了哪些处置措施？', '对{subject}依次采取过哪些措施？'],  # v0.5: no {task}
}

# tool_evidence_recall is asked once per Agent episode (phases 0/2/3/4), each with its own
# distinct correct answer. A phase-agnostic wording like "Agent当时依据什么证据得出结论？"
# would be genuinely ambiguous -- the same question text would map to 4 different gold
# answers within one scenario, with nothing in the text to tell them apart. Each phase
# therefore gets its own narrative-stage-specific wording (domain-neutral via {subject},
# same principle as QUESTION_VARIANTS) so the question text itself disambiguates which of
# the 4 diagnoses it's asking about.
TOOL_EVIDENCE_QUESTION_VARIANTS = {
    0: ['{subject}的问题刚被上报时，Agent是依据哪一项检测数据得出结论的？', '在最初上报阶段，关于{subject}，Agent引用的具体证据是什么？',
        '{subject}的异常刚被发现时，Agent依据的关键检测结果是什么？'],
    2: ['当{subject}的第一次调整被证实没有生效时，Agent依据的是哪项检测数据？', '在{subject}首次干预失败这一步，Agent引用的证据是什么？',
        '发现{subject}的第一次处置没有起作用时，Agent依据了什么具体数据？'],
    # v0.4: phase 3 only shows the fix working (verification is still pending), so the wording
    # no longer says the fix was "confirmed"
    3: ['{subject}换用新措施后，显示情况好转的那次检测给出了什么数据？', 'Agent判断{subject}在新措施后已经好转，依据的是哪项检测数据？',
        '证明{subject}的有效措施起了作用的那次检测，具体数据是什么？'],  # v0.5: the reading that showed the improvement (not the final verification)
    4: ['在{subject}的最终验证阶段，Agent得出结论所依据的关键检测数据是什么？', '关闭{subject}相关任务前，Agent依据哪项关键检测结果确认问题已解决？',
        '{subject}最终验证这一步，支撑Agent结论的关键数据是什么？'],
}

# machine-tool-style display names for dialogue text only (structured fields keep EQ_xxxx/LOT_xxxx)
DISPLAY_EQ = {
    'maintenance': ['CA6140-M1', 'Z3040-M2', 'X6132-M3', 'T68-M4', 'CA6136-M5', 'MJ1640-M6'],
    'quality': ['SMT-REFLOW-01', 'REFLOW-OVEN-02', 'SMT-REFLOW-03', 'N2-REFLOW-04'],  # v0.5: reflow ovens only
    'process': ['INJ-MOLD-05', 'CNC-MC500-06', 'PRESS-LINE-07', 'EXTRUDE-08'],
    'logistics': ['CONV-LINE-09', 'AS-RS-10', 'PALLET-STA-11', 'SORT-HUB-12'],
}
# component/part display names for dialogue text only (structured fields keep
# COMP_SENSOR_xxxx) -- only maintenance and logistics templates actually reference
# {comp}, but a name is defined for every domain for symmetry with DISPLAY_EQ.
DISPLAY_COMP = {
    'maintenance': ['温度传感器', '振动传感器', '冷却液传感器', '主轴轴承', '传动皮带'],
    'quality': ['回流焊温感', '预热传感器'],
    'process': ['压力传感器', '进给传感器'],
    'logistics': ['运输纸箱', '子批次标签', '托盘标签'],
}


def fmt(tmpl, **kw):
    return tmpl.format(**kw)


def write(name, rows):
    p = ROOT / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in rows))


def time_seq(start, step=3):
    """Unbounded turn clock: start+2min, then every `step` minutes (may cross midnight)."""
    t = start + timedelta(minutes=2)
    while True:
        yield t
        t += timedelta(minutes=step)


def shift_start(day, cal, idx):
    _name, hhmm, _hours = CALENDARS[cal][idx]
    h, m = map(int, hhmm.split(':'))
    return datetime(day.year, day.month, day.day, h, m, tzinfo=TZ)


def shift_of(t, cal):
    """(shift_id, start, end) of the calendar shift containing t; ids are keyed by start date."""
    for day in (t.date() - timedelta(days=1), t.date()):
        for idx, (name, _hhmm, hours) in enumerate(CALENDARS[cal]):
            st = shift_start(day, cal, idx)
            en = st + timedelta(hours=hours)
            if st <= t < en:
                return f'{day.isoformat()} {name}', st, en
    raise ValueError(f'{t} not covered by {cal}')


def shift_label(shift_id, st, en):
    end = f'{NEXT_DAY}{en:%H:%M}' if en.date() > st.date() else f'{en:%H:%M}'
    return SHIFT_LABEL.format(shift_id=shift_id, start=f'{st:%H:%M}', end=end)


def anaphorize(report, subject_text, subject_key):
    """Replace the subject's display name in a dense report with an anaphor."""
    return report.replace(subject_text, rng.choice(ANAPHOR[subject_key]))


def pick_session_start(day, cal):
    """Random start inside a random shift, leaving room before the shift starts and ends."""
    idx = rng.randrange(len(CALENDARS[cal]))
    hours = CALENDARS[cal][idx][2]
    return shift_start(day, cal, idx) + timedelta(minutes=rng.randrange(75, hours * 60 - 90, 5))


def pick_occurrence(clock, when):
    """Time the reported event happened: earlier in this shift ('cur') or in the previous one ('prev')."""
    _sid, st, _en = shift_of(clock['start'], clock['cal'])
    if when == 'prev':
        _pid, st, en = shift_of(st - timedelta(minutes=1), clock['cal'])
        span = int((en - st).total_seconds() // 60)
        return st + timedelta(minutes=rng.randrange(30, span - 15))
    span = int((clock['start'] - st).total_seconds() // 60)
    return st + timedelta(minutes=rng.randrange(10, span - 10))


def routine_value(tool_name):
    lo, hi = ROUTINE_VALUE_RANGE.get(tool_name, (0, 1))
    v = rng.uniform(lo, hi)
    return f'{v:.2f}' if tool_name == 'flux_query' else round(v, 1)


def resolve_extra(kind, key, eq, lot, clock):
    """Ground-truth de-linearization payload for a relative-time / vague-referent turn."""
    extra = {}
    if kind == 'time_prev':
        extra['resolved_time'] = clock['prev_start'].isoformat()
    elif kind == 'time_recent':
        extra['resolved_time'] = clock['start'].isoformat()
    elif kind == 'shift_prev':
        _sid, st, _en = shift_of(clock['start'], clock['cal'])
        pid, pst, _pen = shift_of(st - timedelta(minutes=1), clock['cal'])
        extra['resolved_time'] = pst.isoformat()
        extra['resolved_shift'] = pid
    if key:
        extra['resolved_entities'] = [eq if key == 'eq' else lot]
    return extra or None


def on_topic(pool, subject_key):
    """Filter a (text, kind, key) pool to entries whose referenced entity (if any)
    matches what this domain's narrative is actually about, so a lot-flavored lead-in
    ("this lot shouldn't have other issues, right") never gets glued onto a dense
    report that's entirely about equipment, or vice versa."""
    return [c for c in pool if c[2] is None or c[2] == subject_key]


def session_roles(domain, s):
    """(reporter, other) role pairs for session s: the domain owner reports in sessions 1 and 5,
    the middle sessions rotate through the other roles, and the two speakers always differ."""
    owner = DOMAIN_OWNER[domain]
    others = [r for r in ROLES if r[0] != owner]
    reporter = next(r for r in ROLES if r[0] == owner) if s in (1, 5) else others[(s - 2) % len(others)]
    rest = [r for r in ROLES if r != reporter]
    return reporter, rest[(s + 1) % len(rest)]


def build_collab_session(sid, i, s, clock, domain, eq, comp, lot, alarm, task, tool_id, dctx):
    """Phase-1 human<->human hypothesis discussion (unchanged narrative style)."""
    sess = f'{sid}_S{s:02d}'
    phase = s - 1
    (reporter_r, reporter_role), (other_r, other_role) = session_roles(domain, s)
    reporter_id, other_id = f'{reporter_r}{i:04d}', f'{other_r}{i:04d}'

    times = time_seq(clock['start'])
    turns = []
    core_ids = []

    def add(speaker_id, speaker_role, text, turn_type, is_low_density, extra=None):
        did = f'{sess}_D{len(turns) + 1:02d}'
        row = {'dialog_id': did, 'speaker_id': speaker_id, 'speaker_role': speaker_role,
               'timestamp': next(times).isoformat(), 'text': text,
               'mentions': [eq, comp, lot, alarm], 'turn_type': turn_type, 'is_low_density': is_low_density}
        if extra:
            row.update(extra)
        turns.append(row)
        return did

    # 1. routine tool pre-check (usually present, low density)
    if rng.random() < 0.6:
        add(tool_id, 'system_tool', fmt(rng.choice(TOOL_ROUTINE), **dctx), 'tool_call', True)

    # optional context / small-talk turn referencing relative time or vague entity
    if rng.random() < 0.5:
        if s > 1 and rng.random() < 0.45:
            text, kind, key = rng.choice(CONTEXT_TURNS)
            extra = resolve_extra(kind, key, eq, lot, clock)
            add(other_id, other_role, fmt(text, **dctx), 'human', True, extra)
        else:
            add(other_id, other_role, rng.choice(SMALLTALK_OPEN), 'human', True)

    # 2. dense human fact report (core, high density)
    dense_text = fmt(rng.choice(PARAPHRASE[domain][phase]), **dctx)
    human_id = add(reporter_id, reporter_role, dense_text, 'human', False)
    core_ids.append(human_id)

    # 3. routine tool status (phase 1 never carries the key evidence -- that only
    # happens in the Agent episodes)
    add(tool_id, 'system_tool', fmt(rng.choice(TOOL_ROUTINE), **dctx), 'tool_call', True)

    # optional extra clarifying Q&A (low density)
    if rng.random() < 0.25:
        q, a = rng.choice(EXTRA_QA)
        add(other_id, other_role, q, 'human', True)
        add(reporter_id, reporter_role, a, 'human', True)

    # 4. acknowledgement (usually present, low density)
    if rng.random() < 0.9:
        add(other_id, other_role, rng.choice(COLLAB_ACK_POOL), 'human', True)

    # optional closing small talk
    if rng.random() < 0.15:
        add(reporter_id, reporter_role, rng.choice(SMALLTALK_CLOSE), 'human', True)

    if len(turns) < 3:
        add(other_id, other_role, rng.choice(COLLAB_ACK_POOL), 'human', True)

    participants = [{'id': reporter_id, 'role': reporter_role}, {'id': other_id, 'role': other_role},
                    {'id': tool_id, 'role': 'system_tool'}]
    session = {'scenario_id': sid, 'session_id': sess, 'session_start': clock['start'].isoformat(),
               'site_id': clock['site'], 'participants': participants, 'turns': turns}
    return session, core_ids


def build_agent_episode(sid, i, s, clock, domain, eq, comp, lot, alarm, task, agent_id, phase, dctx, canon_stmt,
                        note=None):
    """A ReAct-style user<->Agent diagnostic episode for phases 0/2/3/4.

    Elimination pattern: the Agent plans a check, rules out 1-3 unrelated
    subsystems with routine (low-density, "normal") tool calls, then exactly
    one tool call surfaces the precise datum that actually explains the
    situation (high density), and the Agent synthesizes a conclusion that
    lands on the phase's canonical fact.
    """
    sess = f'{sid}_S{s:02d}'
    (reporter_r, reporter_role), (other_r, other_role) = session_roles(domain, s)
    reporter_id, other_id = f'{reporter_r}{i:04d}', f'{other_r}{i:04d}'
    tool_id = f'SYS_{"EMS" if domain in ("maintenance", "process") else "MES"}_{i:04d}'  # the plant system answering

    times = time_seq(clock['start'])
    turns = []

    def add(speaker_id, speaker_role, text, turn_type, is_low_density, extra=None):
        did = f'{sess}_D{len(turns) + 1:02d}'
        row = {'dialog_id': did, 'speaker_id': speaker_id, 'speaker_role': speaker_role,
               'timestamp': next(times).isoformat(), 'text': text,
               'mentions': [eq, comp, lot, alarm], 'turn_type': turn_type, 'is_low_density': is_low_density}
        if extra:
            row.update(extra)
        turns.append(row)
        return did

    # 1. user_query -- dense: the report/ask is the same informational payload the
    # phase-1 style dense report would have carried, just addressed to the Agent.
    # Phases 0/2/3 always anchor the event to a shift ("上一班交接时说，...") with
    # resolved_time/resolved_shift ground truth -- shift_temporal questions ask about it.
    # Phase 4 keeps the ~30% relative-time / vague-referent lead-in ("上一次报的那个问题呢，...").
    subject_key = SUBJECT_OF_DOMAIN[domain]
    subject_text = dctx[subject_key]
    report = strip_period(fmt(rng.choice(PARAPHRASE[domain][phase]), **dctx))
    uq_prefix, uq_extra, occurrence = '', {}, None
    if phase in SHIFT_PHASES:
        when = rng.choice(['cur', 'prev'])
        occ = pick_occurrence(clock, when)
        occurrence = {'time': occ, 'shift': shift_of(occ, clock['cal']), 'when': when}
        uq_prefix = rng.choice(SHIFT_OCC_PREFIX[when])
        uq_extra = {'resolved_time': occ.isoformat(), 'resolved_shift': occurrence['shift'][0]}
    elif rng.random() < 0.3:
        ptext, kind, key = rng.choice(on_topic(AGENT_QUERY_PREFIX, subject_key))
        uq_prefix = ptext
        uq_extra = resolve_extra(kind, key, eq, lot, clock) or {}

    # cross-turn anaphora: a low-density opener names the subject and the dense report
    # only says "这台设备/它/这一批", so the report cannot be interpreted on its own.
    if phase >= 2 and subject_text in report and not (phase == 4 and uq_prefix) and rng.random() < 0.4:
        add(reporter_id, reporter_role, fmt(rng.choice(ANAPHOR_OPENER), subject=subject_text), 'human', True)
        report = anaphorize(report, subject_text, subject_key)
        uq_extra['resolved_entities'] = sorted(set(uq_extra.get('resolved_entities', [])) |
                                               {eq if subject_key == 'eq' else lot})
    uq_text = uq_prefix + report + rng.choice(AGENT_QUERY_SUFFIX)
    uq_id = add(reporter_id, reporter_role, uq_text, 'user_query', False, uq_extra or None)

    # optional brief acknowledgement before the Agent lays out its plan -- sometimes an
    # anaphoric reference to an earlier report instead of a generic "got it"
    if rng.random() < 0.5:
        if rng.random() < 0.4:
            ctext, kind, key = rng.choice(on_topic(AGENT_THINK_CONTEXT, subject_key))
            extra = resolve_extra(kind, key, eq, lot, clock)
            add(agent_id, 'industrial_agent', fmt(ctext, **dctx), 'agent_think', True, extra)
        else:
            add(agent_id, 'industrial_agent', rng.choice(AGENT_ACK_SHORT), 'agent_think', True)

    if domain == 'maintenance' and phase >= 2:
        family = MAINT_FAMILY[dctx['family']]
        key_tool, key_template = family['tool'], family[phase]
    else:
        key_tool, key_template = AGENT_INFORMATIVE[domain][phase]
    excluded = PROBLEM_TOOLS[domain] | {key_tool}
    if domain == 'maintenance':
        excluded = excluded | {MAINT_FAMILY[dctx['family']]['tool']}
    routine_pool = [t for t in TOOL_NAMES[domain] if t not in excluded]
    rng.shuffle(routine_pool)
    chosen_routine = routine_pool[:rng.randint(1, min(3, len(routine_pool)))]

    # 2. agent_think -- plan (low density: reasoning, not fact)
    first = '依次检查' + '、'.join(TOOL_LABEL[t] for t in chosen_routine) + '，如果都正常' if len(chosen_routine) > 1 \
        else f'检查{TOOL_LABEL[chosen_routine[0]]}，如果正常'
    plan_text = f"我先{first}，再确认{TOOL_LABEL[key_tool]}。"
    add(agent_id, 'industrial_agent', plan_text, 'agent_think', True)

    # 3. routine agent_tool_call(s) -- low density, "normal" (the elimination phase),
    # occasionally interleaved with a short "moving on" think note for a more
    # naturalistic multi-turn trace.
    # v0.5: every tool use is two turns -- the Agent's JSON call and the plant system's JSON
    # observation (generation/tool_json.py); routine observations are all-normal, low density.
    for j, t in enumerate(chosen_routine):
        v = routine_value(t)
        add(agent_id, 'industrial_agent', tool_json.call_json(t, dctx, '24h'), 'agent_tool_call', True, {'tool_name': t})
        obs_at = datetime.fromisoformat(turns[-1]['timestamp'])
        data, limits = tool_json.routine_data(rng, t, v, obs_at)
        message = tool_message(fmt(AGENT_ROUTINE[domain][t], v=v, **dctx))
        add(tool_id, 'system_tool', tool_json.observation_json(rng, t, dctx, obs_at, data, limits, 'normal', message),
            'tool_observation', True, {'tool_name': t})
        if j < len(chosen_routine) - 1 and rng.random() < 0.35:
            add(agent_id, 'industrial_agent', rng.choice(AGENT_FILLER_THINK), 'agent_think', True)

    # optional mid-episode think before the decisive call
    if rng.random() < 0.5:
        add(agent_id, 'industrial_agent', fmt(rng.choice(AGENT_THINK_MID if len(chosen_routine) > 1 else AGENT_THINK_MID_ONE),
                                              last_label=TOOL_LABEL[key_tool]),
            'agent_think', True)

    # 4. the decisive agent_tool_call -- high density, this IS the evidence. v0.4: the quoted
    # measurement time is when the reported event happened (phases 0/2/3, the shift-anchored
    # occurrence) or shortly before the verification session (phase 4). v0.3 quoted the time
    # of the query itself, i.e. *after* the user had already reported the result.
    measured = occurrence['time'] if occurrence else clock['start'] - timedelta(minutes=rng.randrange(20, 120, 5))
    key_text = tool_message(fmt(key_template, ts=f'{measured:%Y-%m-%d %H:%M}', **dctx))
    add(agent_id, 'industrial_agent', tool_json.call_json(key_tool, dctx, '72h' if phase == 4 else '24h'),
        'agent_tool_call', True, {'tool_name': key_tool})
    family = tool_json.MAINT_FAMILIES[DISPLAY_COMP['maintenance'].index(dctx['family'])] if domain == 'maintenance' else None
    data, limits, result = tool_json.key_data(domain, phase, family, dctx, measured)
    key_id = add(tool_id, 'system_tool',
                 tool_json.observation_json(rng, key_tool, dctx, measured, data, limits, result, key_text),
                 'tool_observation', False, {'tool_name': key_tool})

    # 5. agent_response -- synthesis, lands on the phase's canonical fact
    resp_id = add(agent_id, 'industrial_agent', canon_stmt, 'agent_response', False)

    # task-level constraints (phase 3) / open item (phase 4), stated by the engineer -- dense
    note_id = add(other_id, other_role, note, 'human', False) if note else None

    # optional follow-up round -- a more complex interaction with an extra low-density
    # question/answer exchange after the conclusion but before the user signs off. v0.4: the
    # Agent's reply is spoken to the user, so it is an agent_response (low density), not a think.
    if rng.random() < 0.3:
        fq, fa = rng.choice(AGENT_FOLLOWUP)
        add(other_id, other_role, fq, 'human', True)
        add(agent_id, 'industrial_agent', fmt(fa, **dctx), 'agent_response', True)

    # 6. user_ack -- low density. v0.4: when the other engineer just stated the constraints /
    # open item, the reporter acknowledges it (v0.3 had the same person thank themself).
    add(*((reporter_id, reporter_role) if note else (other_id, other_role)), rng.choice(NOTE_ACK_POOL if note else ACK_POOL),
        'human', True)
    if rng.random() < 0.2:
        add(reporter_id, reporter_role, rng.choice(SMALLTALK_CLOSE), 'human', True)

    core_ids = [uq_id, key_id, resp_id]
    participants = [{'id': reporter_id, 'role': reporter_role}, {'id': other_id, 'role': other_role},
                    {'id': agent_id, 'role': 'industrial_agent'}]
    session = {'scenario_id': sid, 'session_id': sess, 'session_start': clock['start'].isoformat(),
               'site_id': clock['site'], 'participants': participants, 'turns': turns}
    agent_evidence = {'dialog_id': key_id, 'text': key_text, 'tool_name': key_tool,
                      'uq_id': uq_id, 'occurrence': occurrence, 'note_id': note_id}
    return session, core_ids, agent_evidence


def tool_message(text):
    """The observation's one-line `message`: a v0.4-style tool sentence without its '<tool>返回：' prefix."""
    msg = text.split('返回：', 1)[1]
    return msg


def strip_period(s):
    return s[:-1] if s.endswith('。') else s


def build_answer(cat, domain, canon, task, eq, comp, lot, subject, evs, memids, constraints, tool_evidence=None,
                 occurrence=None, unanswerable_kind=None):
    aux = {'equipment_id': eq, 'component_id': comp, 'lot_id': lot, 'task_id': task, 'event_ids': evs}
    if cat == 'shift_temporal':
        aux.update(shift_id=occurrence['shift'][0], occurred_at=occurrence['time'].isoformat(),
                   anchor=occurrence['when'])
        return {'type': 'text', 'reference': shift_label(*occurrence['shift']), 'aux': aux}
    if cat == 'unanswerable':
        return {'type': 'text', 'reference': NO_INFO_ANSWER, 'aux': {'unanswerable_type': unanswerable_kind}}
    bare = [strip_period(c) for c in canon]
    # the effective fix as an action, without the transient state it was reported in
    # ("等待连续运行验证"): by as_of the verification has passed, so a gold still saying
    # "awaiting verification" made a correct "verified" answer look contradictory (v0.3)
    fix = bare[3].split('，')[0]
    if cat == 'single_hop_fact':
        ref = canon[0]
    elif cat == 'temporal_order':
        # phase2 is always the failed/temporary measure and phase3 the one that
        # eventually works, but *what kind* of measure that is differs per domain
        # (parameter tweak, threshold relaxation, label rescan, ...), so the
        # labels here stay generic instead of naming a specific action.
        ref = f'第一次处置措施（{bare[2]}）发生在先，随后生效的处置措施（{fix}）发生在后。'
    elif cat == 'state_tracking':
        ref = canon[4]
    elif cat == 'failed_intervention':
        # v0.4: only the failed measure -- the later fix is not asked for, and a gold carrying it
        # could mark a complete answer as missing something
        ref = canon[2]
    elif cat == 'cross_role_dependency':
        owner = ROLE_NAMES[dict(ROLES)[DOMAIN_OWNER[domain]]]
        ref = f'最先报告问题的是{owner}，最后提请确认结案的也是{owner}。'  # v0.5: roles only
    elif cat == 'working_memory_snapshot':
        return {'type': 'working_memory', 'active_memory_ids': memids[4:5],
                'pending_item_ids': [], 'constraints': [], 'aux': aux}
    elif cat == 'constraint_recall':
        ref = CONSTRAINT_JOIN.join(constraints)
    elif cat == 'superseded_fact':
        ref = f'没有奏效的措施是“{bare[2]}”，之后改为“{fix}”，并最终通过验证。'
    elif cat == 'multi_hop_event':
        ref = '；'.join(bare) + '。'
    elif cat == 'conditioned_parameter':
        # v0.4: the quality quarantine is a measure taken on the subject too
        contain = CONTAINMENT.get(domain, '')
        ref = f'针对{subject}先后采取的措施：{contain}{bare[2]}（没有奏效）；随后{fix}（此后验证通过）。'
    elif cat == 'tool_evidence_recall':
        ref = tool_evidence
    else:
        ref = canon[0]
    return {'type': 'text', 'reference': ref, 'aux': aux}


def main(n_scenarios=200, q_per=39):
    scenarios, sessions, events, memories, wms, questions = [], [], [], [], [], []
    as_of = '2026-06-30T12:00:00+08:00'
    for i in range(1, n_scenarios + 1):
        sid = f'SC_GEN_{i:04d}'
        eq, lot, comp, alarm = f'EQ_{i:04d}', f'LOT_{i:04d}', f'COMP_SENSOR_{i:04d}', f'ALARM_{200 + i}'
        domain, _label = DOMAINS[(i - 1) % len(DOMAINS)]
        task = f'WO_{i:04d}'
        tool_id = f'SYS_{"EMS" if domain in ("maintenance", "process") else "MES"}_{i:04d}'
        agent_id = f'AGENT_{i:04d}'

        # DISPLAY_EQ only has 4-6 model names per domain, cycled by modulus -- with 25
        # scenarios per domain that means the *same* display name (e.g. PRESS-LINE-07)
        # gets reused across up to 25 scenarios. Since 8 of the 11 question categories
        # key their question text on {subject} (the display name) with no {task} to
        # disambiguate, a bare model name is not a unique identifier: "What is
        # PRESS-LINE-07's current recorded state?" would be genuinely ambiguous across
        # every scenario that reused that name. Appending the (globally unique, 1..100)
        # scenario index as a serial suffix keeps the "looks like a real asset tag"
        # flavor while guaranteeing every scenario's display name is unique corpus-wide.
        # dev_ext scenarios (101-120) are numbered 301-320 and test_ext scenarios (121-200)
        # 601-680 in display names, so their suffixes collide neither with each other, with
        # scenarios 1-100, nor with any look-alike range (101-200, 401-420, 701-780).
        serial = i if i <= 100 else i + 200 if i <= 120 else i + 480
        # v0.4: the five sessions span about two weeks, not five months (a lot quarantined from
        # February to June, or a 72-hour check a month after the fix, read as implausible).
        # Gaps of >= 2 days keep a previous-shift occurrence after the previous session, and the
        # verification comes >= 4 days after the fix so its 72-hour run fits in between.
        # v0.5: the plant history spans a year (first reports from 2025-07 to 2026-05) so questions asked on
        # as_of reach back up to a year; one incident takes 1-5 weeks with uneven gaps (a failed fix can
        # recur weeks later, the verification can come a week after the fix).
        base_day = datetime(2025, 7, 1).date() + timedelta(days=(i * 37) % 320)
        if (base_day + timedelta(days=1)).month != base_day.month:
            base_day -= timedelta(days=1)  # a night shift would carry the first report into next month
        session_days = [base_day]
        for lo, hi in [(2, 5), (3, 14), (2, 7), (4, 10)]:
            session_days.append(session_days[-1] + timedelta(days=rng.randint(lo, hi)))
        disp_eq = f'{DISPLAY_EQ[domain][(i - 1) % len(DISPLAY_EQ[domain])]}-{serial:03d}'
        # the lot number encodes its production month, which is the month of the first report
        disp_lot = f'LOT{base_day:%Y%m}-{serial:03d}'
        # comp previously leaked the raw COMP_SENSOR_xxxx primary key straight into
        # dialogue text (e.g. "怀疑是部件COMP_SENSOR_0001导致的") -- give it the same
        # cosmetic display-name treatment already applied to eq/lot.
        comp_name = DISPLAY_COMP[domain][(i - 1) % len(DISPLAY_COMP[domain])]
        disp_comp = f'{comp_name}#{serial:03d}'
        dctx = {'eq': disp_eq, 'comp': disp_comp, 'lot': disp_lot, 'alarm': alarm, 'task': task, 'family': comp_name}
        if domain == 'maintenance':
            dctx.update(symptom=MAINT_FAMILY[comp_name]['symptom'], measure=fmt(MAINT_FAMILY[comp_name]['measure'], **dctx))
        canon = [fmt(t, **dctx) for t in CANON[domain]]
        # question/answer text must name the same entity the dialogue turns actually
        # display (dctx['eq']/dctx['lot']), not the raw EQ_xxxx/LOT_xxxx id -- otherwise
        # the question string shares no tokens with its own evidence turns.
        subject = dctx['eq'] if SUBJECT_OF_DOMAIN[domain] == 'eq' else dctx['lot']
        # look-alike ID for unseen-entity questions: same naming scheme, suffix 101-200 (401-420
        # for dev_ext, 701-780 for test_ext) is never used by any scenario, so no history can contain it.
        unseen_subject = (f'{DISPLAY_EQ[domain][(i - 1) % len(DISPLAY_EQ[domain])]}-{serial + 100:03d}'
                          if SUBJECT_OF_DOMAIN[domain] == 'eq' else f'LOT{base_day:%Y%m}-{serial + 100:03d}')
        site = f'SITE_{(i % 5) + 1:02d}'
        cal = SITE_CALENDAR[site]
        constraints = [fmt(t, subject=subject, task=task) for t in CONSTRAINT_TEMPLATES]
        pending = fmt(PENDING_TEMPLATE, task=task, subject=subject)
        notes = {3: fmt(CONSTRAINT_NOTE, constraints=CONSTRAINT_JOIN.join(constraints)),
                 4: fmt(PENDING_NOTE, pending=pending)}

        scenarios.append({'scenario_id': sid, 'domain': domain, 'industry': 'synthetic_discrete_manufacturing',
                           'enterprise_id': 'ENT_SYN', 'site_id': site, 'production_calendar_id': cal,
                           'area_id': f'AREA_{(i % 8) + 1:02d}', 'equipment_ids': [eq],
                           'time_span': {'start': '2025-07-01', 'end': '2026-06-30'}, 'synthetic': True})

        dids, core_ids_by_session = [], []
        agent_evidence_by_session = {}
        prev_start = None
        for s in range(1, 6):
            phase = s - 1
            start = pick_session_start(session_days[s - 1], cal)
            clock = {'start': start, 'prev_start': prev_start or start, 'cal': cal, 'site': site}
            occurrence = None
            if phase == 1:
                session, core_ids = build_collab_session(sid, i, s, clock, domain, eq, comp, lot,
                                                           alarm, task, tool_id, dctx)
            else:
                session, core_ids, agent_evidence = build_agent_episode(sid, i, s, clock, domain,
                                                                          eq, comp, lot, alarm, task, agent_id, phase,
                                                                          dctx, canon[phase], note=notes.get(phase))
                agent_evidence_by_session[s] = agent_evidence
                occurrence = agent_evidence['occurrence']
            sessions.append(session)
            dids.append(core_ids[0])
            core_ids_by_session.append(core_ids)
            prev_start = start

            # an event happens when the shift-anchored report says it did; otherwise when it is reported
            ev_start = occurrence['time'] if occurrence else datetime.fromisoformat(
                next(t['timestamp'] for t in session['turns'] if t['dialog_id'] == core_ids[0]))
            ev = f'EV_GEN_{i:04d}_{s:02d}'
            events.append({'event_id': ev, 'scenario_id': sid, 'event_type': EVENT_TYPES[domain][s - 1], 'subject_id': eq,
                            'object_id': comp if s in (2, 4) else lot,
                            'event_time': {'start': ev_start.isoformat()}, 'shift_id': shift_of(ev_start, cal)[0],
                            'state_before': STATES_BEFORE[domain][s - 1], 'state_after': STATES_AFTER[domain][s - 1],
                            # the constraint / open-item note stated in phases 3-4 belongs to that event
                            'source_dialog_ids': core_ids + ([agent_evidence['note_id']] if phase in notes else []),
                            'verification_status': VERIF_STATUS[s - 1]})

        memids = []
        for m, statement in enumerate(canon, 1):
            mid = f'MEM_GEN_{i:04d}_{m:02d}'
            memids.append(mid)
            memories.append({'memory_id': mid, 'scenario_id': sid, 'memory_type': 'episodic', 'statement': statement,
                              'subject_ids': [eq, comp, lot], 'event_id': f'EV_GEN_{i:04d}_{m:02d}',
                              'status': 'active' if m >= 4 else 'historical', 'confidence': 'verified',
                              'source_dialog_ids': core_ids_by_session[m - 1],
                              'supersedes': [memids[-2]] if m == 4 else [], 'superseded_by': []})
            if m == 4:
                # MEM_04 (effective fix) supersedes MEM_03 (failed intervention) -- mirror the
                # edge onto MEM_03.superseded_by so a superseded_by-based staleness lookup
                # actually finds it (it was previously left empty, an unresolvable dangling
                # reference). MEM_05 (verification) confirms MEM_04 rather than superseding
                # it, so MEM_04.superseded_by stays empty -- it remains the active record,
                # consistent with its 'active' status and its presence in
                # working_memory.active_facts.
                memories[-2]['superseded_by'] = [mid]

        todo_id = f'TODO_GEN_{i:04d}'
        note_ids = [agent_evidence_by_session[p + 1]['note_id'] for p in sorted(notes)]
        wms.append({'working_memory_id': f'WM_GEN_{i:04d}', 'scenario_id': sid,
                    'snapshot_time': as_of, 'task_id': task,
                    'active_facts': [{'memory_id': memids[4], 'status': 'active', 'priority': 'high'}],
                    'pending_items': [{'item_id': todo_id, 'description': pending,
                                        'owner_role': 'maintenance_engineer', 'status': 'open'}],
                    'constraints': constraints,
                    'source_dialog_ids': [d for ids in core_ids_by_session for d in ids] + note_ids})

        # distinct wordings per category within a scenario (independent rng.choice gave
        # ~12% exact duplicate questions in v0.1)
        wording = {c: rng.sample(v, q_per // len(CATS)) for c, v in QUESTION_VARIANTS.items()}
        for q in range(q_per):
            cat = CATS[q % len(CATS)]
            k = q // len(CATS)  # k-th question of this category within the scenario
            multi = cat in MULTI_EVENT_CATS
            required_entities = [eq, comp, lot]
            if cat == 'shift_temporal':
                s_pick = SHIFT_PHASES[k % len(SHIFT_PHASES)] + 1
                info = agent_evidence_by_session[s_pick]
                question = fmt(rng.choice(SHIFT_QUESTION_VARIANTS[s_pick - 1]), subject=subject)
                evs = [f'EV_GEN_{i:04d}_{s_pick:02d}']
                evidence = [info['uq_id'], info['dialog_id']]  # v0.5: the observation carries the measured time
                answer = build_answer(cat, domain, canon, task, eq, comp, lot, subject, evs, memids, constraints,
                                       occurrence=info['occurrence'])
            elif cat == 'unanswerable':
                kind = UNANSWERABLE_KINDS[k % len(UNANSWERABLE_KINDS)]
                if kind == 'unseen_entity':
                    tmpl = rng.choice(QUESTION_VARIANTS[rng.choice(UNSEEN_ENTITY_CATS)])
                    question = fmt(tmpl, subject=unseen_subject, task=task, as_of=as_of)
                    required_entities = []
                elif kind == 'unrecorded_attribute':
                    question = fmt(rng.choice(UNRECORDED_ATTRIBUTE_QUESTIONS), subject=subject)
                else:
                    question = fmt(rng.choice(FALSE_PREMISE_QUESTIONS), subject=subject)
                evs, evidence = [], []
                answer = build_answer(cat, domain, canon, task, eq, comp, lot, subject, evs, memids, constraints,
                                       unanswerable_kind=kind)
            elif cat == 'tool_evidence_recall':
                # phase-specific wording (see TOOL_EVIDENCE_QUESTION_VARIANTS) so the
                # question itself disambiguates which of the 4 Agent-episode diagnoses
                # it's asking about, instead of one generic phrasing that would map to
                # 4 different gold answers within the same scenario.
                s_pick = AGENT_PHASE_SESSIONS[q % len(AGENT_PHASE_SESSIONS)]
                phase = s_pick - 1
                info = agent_evidence_by_session[s_pick]
                question = fmt(rng.choice(TOOL_EVIDENCE_QUESTION_VARIANTS[phase]), subject=subject)
                evs = [f'EV_GEN_{i:04d}_{s_pick:02d}']
                evidence = [info['dialog_id']]
                answer = build_answer(cat, domain, canon, task, eq, comp, lot, subject, evs, memids, constraints,
                                       tool_evidence=info['text'])
            elif cat == 'constraint_recall':
                # the constraints are stated once, in the phase-3 episode
                question = fmt(wording[cat][k], subject=subject, task=task, as_of=as_of)
                evs = [f'EV_GEN_{i:04d}_04']
                evidence = [agent_evidence_by_session[4]['note_id']]
                answer = build_answer(cat, domain, canon, task, eq, comp, lot, subject, evs, memids, constraints)
            elif multi:
                question = fmt(wording[cat][k], subject=subject, task=task, as_of=as_of)
                evs = [f'EV_GEN_{i:04d}_{j:02d}' for j in range(1, 6)]
                evidence = [d for ids in core_ids_by_session for d in ids]
                answer = build_answer(cat, domain, canon, task, eq, comp, lot, subject, evs, memids, constraints)
                if cat == 'working_memory_snapshot':
                    answer['pending_item_ids'] = [todo_id]
                    evidence = evidence + note_ids
            else:
                question = fmt(wording[cat][k], subject=subject, task=task, as_of=as_of)
                idx = ANCHOR_PHASE[cat] + 1
                evs = [f'EV_GEN_{i:04d}_{idx:02d}']
                evidence = core_ids_by_session[idx - 1]
                answer = build_answer(cat, domain, canon, task, eq, comp, lot, subject, evs, memids, constraints)
            questions.append({'question_id': f'Q_GEN_{i:04d}_{q + 1:02d}', 'scenario_id': sid, 'question': question,
                               'category': cat, 'difficulty': DIFFICULTY[cat], 'answerable': cat != 'unanswerable',
                               'as_of': as_of, 'answer': answer, 'evidence': evidence,
                               'required_entities': required_entities, 'required_events': evs})

    # dev_ext (scenarios 101-120) extends the dev pool to 30 scenarios for a dev K=30 setting;
    # test_ext (121-200) extends test to 90 held-out scenarios, so a trained method can be
    # evaluated at K=30 without touching train. Generation is sequential, so scenarios 1-120
    # are byte-identical to the 120-scenario corpus.
    for split, s_lo, s_hi in [('train', 0, 80), ('dev', 80, 90), ('test', 90, 100), ('dev_ext', 100, 120),
                              ('test_ext', 120, 200)]:
        d = ROOT / split
        d.mkdir(parents=True, exist_ok=True)
        write(f'{split}/scenarios.jsonl', scenarios[s_lo:s_hi])
        write(f'{split}/sessions.jsonl', sessions[s_lo * 5:s_hi * 5])
        write(f'{split}/events.jsonl', events[s_lo * 5:s_hi * 5])
        write(f'{split}/memory_units.jsonl', memories[s_lo * 5:s_hi * 5])
        write(f'{split}/working_memory.jsonl', wms[s_lo:s_hi])
        write(f'{split}/questions.jsonl', questions[s_lo * q_per:s_hi * q_per])
    # plant configuration a memory system may read (like an MES shift calendar); not dialogue content
    (ROOT / 'production_calendar.json').write_text(json.dumps(
        {'site_calendar': SITE_CALENDAR,
         'calendars': {c: [{'shift': n, 'start': h, 'hours': d} for n, h, d in v] for c, v in CALENDARS.items()}},
        ensure_ascii=False, indent=2))

    print(json.dumps({'scenarios': n_scenarios, 'questions': len(questions), 'sessions': len(sessions),
                       'events': len(events), 'memory_units': len(memories), 'working_memory': len(wms),
                       'root': str(ROOT)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
