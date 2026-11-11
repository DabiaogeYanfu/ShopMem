"""Deterministically generate a synthetic manufacturing working-memory corpus (English).

This is the English-language sibling of generate_dataset.py. Structure, IDs,
turn_type/speaker_role values, probabilities, and all generation logic are
byte-for-byte identical to the Chinese generator -- only the natural-language
string pools (CANON/PARAPHRASE/AGENT_ROUTINE/AGENT_INFORMATIVE/QUESTION_VARIANTS/
ACK_POOL/SMALLTALK_*/CONTEXT_TURNS/... and a handful of inline answer/constraint
templates inside build_answer()/main()) have been rewritten as idiomatic English
rather than translated literally. Schema field names, turn_type/speaker_role
enum values, event_type/state vocabulary, entity IDs (EQ_0091/LOT_0091), and
timestamp formats are untouched -- they were already language-neutral.

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

v0.2 additions (same logic as generate_dataset.py):
- Production calendar. Every site runs a shift calendar (CALENDARS / SITE_CALENDAR,
  also written to production_calendar.json) and every session starts at a random
  time inside a random shift. The user_query of phases 0/2/3 always anchors the
  event to a shift ("Earlier this shift: ..." / "Per the handoff from the last
  shift: ..."), carrying resolved_time and resolved_shift ground truth; the
  shift_temporal category asks which shift the event happened in, which is only
  answerable with the site's calendar.
- Unanswerable questions (answerable=false): an unseen look-alike entity, an
  attribute the dialogue never records, and a false premise.
- Cross-turn anaphora: in phases 2-4 the dense report sometimes says "this machine/
  it/this lot" and names the entity only in a preceding low-density turn.
"""
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import tool_json

ROOT = Path(__file__).resolve().parents[1] / 'data' / 'generated-EN'
random.seed(20260914)
rng = random.Random(20260914)

ROLES = [('op', 'operator'), ('eng', 'maintenance_engineer'), ('qa', 'quality_engineer'), ('proc', 'process_engineer')]
# v0.4: each domain has an owner role who reports the problem (session 1) and asks to close
# the task (session 5); see generate_dataset.py
DOMAIN_OWNER = {'maintenance': 'eng', 'quality': 'qa', 'process': 'proc', 'logistics': 'op'}
ROLE_NAMES = {'operator': 'operator', 'maintenance_engineer': 'maintenance engineer', 'quality_engineer': 'quality engineer',
              'process_engineer': 'process engineer'}
DOMAINS = [('maintenance', 'Equipment Maintenance'), ('quality', 'Quality Anomaly'),
           ('process', 'Process Parameter'), ('logistics', 'Material Traceability')]
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
# boundaries differ between sites and never sit on midnight, so "last shift" can only be
# resolved with the site's calendar, not with clock-time common sense.
TZ = timezone(timedelta(hours=8))
CALENDARS = {
    'CAL_3SHIFT': [('Day shift', '07:30', 8), ('Swing shift', '15:30', 8), ('Night shift', '23:30', 8)],
    'CAL_2SHIFT': [('Day shift', '08:00', 12), ('Night shift', '20:00', 12)],
}
SITE_CALENDAR = {'SITE_01': 'CAL_3SHIFT', 'SITE_02': 'CAL_3SHIFT', 'SITE_03': 'CAL_3SHIFT',
                 'SITE_04': 'CAL_2SHIFT', 'SITE_05': 'CAL_2SHIFT'}
NEXT_DAY = 'next day '
SHIFT_LABEL = '{shift_id} ({start}–{end})'

# canonical (precise) fact per domain/phase -- used for memory_units + answers + the
# Agent's final agent_response turn (the episode's conclusion must land on the same
# canonical wording the rest of the pipeline already keys off of).
CANON = {
    # v0.4: maintenance wording is filled per fault family (MAINT_FAMILY: {symptom}, {measure})
    # so the alarm, the failed measure and the readings all concern the same failing component.
    'maintenance': [
        '{eq} tripped alarm {alarm} ({symptom}).',
        "Initial inspection points to the {comp}, but it's not confirmed yet.",
        '{measure} brought it back briefly, then the problem came back.',
        'Replacing the {comp} fixed it; waiting on a continuous-run verification.',
        '{eq} passed the continuous-run verification; task {task} can be closed.',
    ],
    # v0.4: a quarantined lot is reworked, not "fixed" by a machine setting, and the acceptance
    # threshold is never relaxed; the root cause is a drifting preheat sensor on the machine.
    'quality': [
        '{lot} produced on {eq} came back with solder void rate out of spec; quality engineering has quarantined it.',
        'Re-check found the actual preheat zone temperature on {eq} running low, likely a drifting {comp}; root cause still unconfirmed.',
        'Temporarily raising the preheat setpoint on {eq} helped the rework of {lot} briefly, but the spot-check void rate went out of spec again.',
        'Replaced the {comp} and recalibrated the preheat zone; the rework of {lot} passed re-inspection and is pending release.',
        '{lot} passed all pre-release re-inspections; task {task} can be closed.',
    ],
    # v0.4: every conclusion is backed by the tool that measures it (yield, first-article
    # inspection items, Cpk) instead of reusing yield figures for all of them.
    'process': [
        '{eq} yield has dropped abnormally, below the 85% target.',
        "Initial suspicion is that {eq}'s preheat settings don't match material lot {lot}.",
        'The first preheat temperature increase on {eq} brought yield back briefly, then it slipped again.',
        'Recipe updated on {eq} for material {lot} and first article confirmed; waiting on stability verification.',
        '{eq} passed stability verification and the process capability review; task {task} can be closed.',
    ],
    # v0.4: one consistent count (12 cartons) and the final check is the inventory count the
    # previous phase said it was waiting for.
    'logistics': [
        'Material lot {lot} is missing a complete traceability record ahead of staging at {eq}.',
        "Reconciliation suspects the label on the {comp} doesn't match its lot.",
        "Backfilling the label data for {lot} did not solve it: one carton still won't match its lot association.",
        "Re-scanned and corrected {lot}'s lot association; waiting on inventory count confirmation.",
        'Inventory count confirmed all 12 cartons of {lot} match their lot association; task {task} can be closed.',
    ],
}

# v0.4 maintenance fault families, keyed by the scenario's component (see generate_dataset.py)
MAINT_FAMILY = {
    'temperature sensor': {
        'tool': 'temperature_query', 'symptom': 'spindle over-temperature', 'measure': 'Lowering the spindle speed on {eq}',
        2: "temperature_query returned: after the speed reduction, {eq} spindle temperature reading dropped for a while, then climbed back to 81°C, over the 75°C alarm limit -- the adjustment didn't hold, checked at {ts}.",
        3: 'temperature_query returned: after replacing the {comp}, {eq} spindle temperature reads a steady 58°C, matching the 57°C from a handheld thermometer, checked at {ts}.',
        4: 'temperature_query returned: {eq} spindle temperature read a steady 56-60°C over 72 hours of continuous run, never above the 75°C limit, verified at {ts}.'},
    'coolant sensor': {
        'tool': 'coolant_level_query', 'symptom': 'low coolant level', 'measure': 'Topping up the coolant on {eq}',
        2: "coolant_level_query returned: after the top-up, {eq} coolant level reading recovered for a while, then dropped to 32% again, below the 60% minimum, while the sight glass shows a normal level -- the fix didn't hold, checked at {ts}.",
        3: 'coolant_level_query returned: after replacing the {comp}, {eq} coolant level reads 88%, matching the sight glass, checked at {ts}.',
        4: 'coolant_level_query returned: {eq} coolant level read a steady 85%-90% over 72 hours of continuous run, no low-level alarms, verified at {ts}.'},
    'vibration sensor': {
        'tool': 'vibration_query', 'symptom': 'spindle vibration over limit', 'measure': "Re-tightening the mounting bolts of {eq}'s vibration sensor",
        2: "vibration_query returned: after re-tightening, {eq} vibration reading was normal for a while, then jumped to 4.1mm/s again, over the 2.5mm/s alarm threshold, while a handheld meter measured only 1.3mm/s -- the fix didn't hold, checked at {ts}.",
        3: 'vibration_query returned: after replacing the {comp}, {eq} vibration reads 1.2mm/s, matching the handheld meter and below the 2.5mm/s alarm threshold, checked at {ts}.',
        4: 'vibration_query returned: {eq} vibration read a steady 1.1-1.3mm/s over 72 hours of continuous run, never above the 2.5mm/s threshold, verified at {ts}.'},
    'spindle bearing': {
        'tool': 'vibration_query', 'symptom': 'spindle vibration over limit', 'measure': "Adjusting the preload on {eq}'s spindle bearing",
        2: "vibration_query returned: after the preload adjustment, {eq} vibration dropped to 2.1mm/s for a while, then climbed back to 3.8mm/s, over the 2.5mm/s alarm threshold -- the adjustment didn't hold, checked at {ts}.",
        3: 'vibration_query returned: after replacing the {comp}, {eq} vibration dropped to 1.1mm/s, below the 2.5mm/s alarm threshold, checked at {ts}.',
        4: 'vibration_query returned: {eq} vibration held a steady 1.0-1.3mm/s over 72 hours of continuous run, never above the 2.5mm/s threshold, verified at {ts}.'},
    'drive belt': {
        'tool': 'vibration_query', 'symptom': 'vibration over limit with abnormal noise', 'measure': "Re-tensioning {eq}'s drive belt",
        2: "vibration_query returned: after re-tensioning, {eq} vibration was normal for a while, then climbed back to 3.5mm/s with belt slip, over the 2.5mm/s alarm threshold -- the adjustment didn't hold, checked at {ts}.",
        3: 'vibration_query returned: after replacing the {comp}, {eq} vibration dropped to 1.2mm/s and the abnormal noise is gone, checked at {ts}.',
        4: 'vibration_query returned: {eq} vibration held a steady 1.1-1.4mm/s over 72 hours of continuous run, never above the 2.5mm/s threshold, verified at {ts}.'},
}

# dialogue surface paraphrases per domain/phase (index 0 == canonical wording).
# Reused both as the phase-1 human dense report and as the basis for an Agent
# episode's opening user_query (phase 1 discussion excluded). Every phase across
# every domain carries at least one {eq}/{lot}/{comp} placeholder.
# v0.4: phases 0/2/3 carry a shift anchor, so no variant says "just" (it contradicted a
# previous-shift anchor); phase-4 variants propose closing the task instead of stating it closed.
PARAPHRASE = {
    'maintenance': [
        ['{eq} tripped alarm {alarm} ({symptom}).', '{eq} tripped an alarm, code {alarm}, {symptom}.',
         'Heads up, {eq} threw alarm {alarm} for {symptom}.', "{eq}'s alarming, code {alarm}, showing {symptom} -- noting it down."],
        ["Initial inspection points to the {comp}, but it's not confirmed yet.",
         "Took a look, might be the {comp}, still need to verify.",
         "Early read is the {comp} is off -- flagging it as a suspect.",
         "Suspecting the {comp}, but can't call it yet."],
        ['{measure} brought it back briefly, then the problem came back.',
         'Tried {measure_lc}, looked fine at first, but it acted up again shortly after.',
         '{measure} only held for a short while before it recurred.',
         'It was fine for a bit after {measure_lc}, then the issue came right back.'],
        ['Replacing the {comp} fixed it; waiting on a continuous-run verification.',
         'Swapped out the {comp} -- looks stable now, keeping an eye on it a while longer.',
         'Put in a new {comp}, no alarms since, waiting on the continuous-run check.',
         "The {comp} has been replaced, machine's back up, just need to verify."],
        ['{eq} passed the continuous-run verification; task {task} can be closed.',
         "Verification's done -- {eq} has been steady, {task} can be closed out, right?",
         'No issues from {eq} the last few days, {task} can wrap up.',
         'Verification came back clean, {task} can be closed.'],
    ],
    'quality': [
        ['{lot} produced on {eq} came back with solder void rate out of spec; quality engineering has quarantined it.',
         '{lot} run on {eq} is over the void rate limit -- quarantine raised.',
         'Caught {lot} failing on solder void rate, quarantine already raised.',
         "{eq}'s output on {lot} is over the void limit, quarantine's already raised."],
        ['Re-check found the actual preheat zone temperature on {eq} running low, likely a drifting {comp}; root cause still unconfirmed.',
         "Rechecked it -- actual preheat temp on {eq} is low, maybe the {comp} is drifting, root cause isn't pinned down yet.",
         "Suspecting the {comp} reads wrong -- {eq}'s preheat zone is actually running low, still confirming.",
         "Looks like a drifting {comp} is leaving {eq}'s preheat zone too cool, exact reason still pending."],
        ['Temporarily raising the preheat setpoint on {eq} helped the rework of {lot} briefly, but the spot-check void rate went out of spec again.',
         'Bumped up the preheat setpoint on {eq}, the rework of {lot} looked okay at first, then the spot-check failed again.',
         "Raising {eq}'s preheat setpoint only held for a while -- the rework of {lot} failed the spot-check again.",
         'The rework of {lot} held up short-term after raising the preheat setpoint on {eq}, but the void rate is over limit again.'],
        ['Replaced the {comp} and recalibrated the preheat zone; the rework of {lot} passed re-inspection and is pending release.',
         'Swapped the {comp} and recalibrated the preheat zone, the rework of {lot} passed re-inspection, waiting on release.',
         'The {comp} is replaced and the preheat zone recalibrated, the re-check on the rework of {lot} passed, pending release.',
         'Replaced the {comp} and recalibrated preheat -- the re-inspection of the rework of {lot} passed, now pending release.'],
        ['{lot} passed all pre-release re-inspections; task {task} can be closed.',
         "{lot}'s pre-release re-checks all passed, {task} can be closed, right?",
         "Re-inspections all came back good, {lot}'s {task} can be closed out.",
         'All pre-release re-checks on {lot} passed, task {task} can be closed.'],
    ],
    'process': [
        ['{eq} yield has dropped abnormally, below the 85% target.',
         "{eq}'s yield has dropped, below the 85% target.",
         "Found {eq}'s yield going down, already under the 85% target.",
         "{eq}'s yield is sliding abnormally, under the 85% target -- noting it."],
        ["Initial suspicion is that {eq}'s preheat settings don't match material lot {lot}.",
         "Suspecting {eq}'s preheat parameters don't match this batch of material, {lot}.",
         "Thinking {eq}'s parameters and {lot}'s material might not be a good match.",
         "Initial read is {eq}'s preheat parameters don't match material {lot}, still confirming."],
        ['The first preheat temperature increase on {eq} brought yield back briefly, then it slipped again.',
         'Yield on {eq} picked up right after the first preheat temperature increase, then dropped again shortly after.',
         'Yield on {eq} recovered briefly after raising the preheat temperature, then fell again not long after.',
         'The first preheat temperature increase on {eq} only gave a short-lived improvement before yield dropped again.'],
        ['Recipe updated on {eq} for material {lot} and first article confirmed; waiting on stability verification.',
         "{eq}'s recipe is updated for {lot}, first article's confirmed, just waiting on stability verification.",
         'Updated the parameter recipe on {eq} for {lot}, first article passed, stability verification left.',
         'Parameter recipe for {lot} updated on {eq} and first article confirmed, waiting on the verification result.'],
        ['{eq} passed stability verification and the process capability review; task {task} can be closed.',
         "{eq}'s stability verification and capability review both passed, {task} can be closed, right?",
         "Review shows {eq}'s process capability meets spec and it's running stable, {task} can wrap up.",
         '{eq} passed stability verification and the capability review, task {task} can be closed.'],
    ],
    'logistics': [
        ['Material lot {lot} is missing a complete traceability record ahead of staging at {eq}.',
         '{lot} is missing traceability records ahead of staging at {eq}.',
         "Found {lot}'s traceability record incomplete before going live at {eq}.",
         "{eq}'s staging can't find a complete trace record for {lot}."],
        ["Reconciliation suspects the label on the {comp} doesn't match its lot.",
         "Finished reconciling -- suspecting the label on the {comp} doesn't match its lot.",
         "Suspecting the {comp}'s label was applied to the wrong lot, still verifying.",
         "Initial read is the label on the {comp} doesn't match its lot, currently verifying."],
        ["Backfilling the label data for {lot} did not solve it: one carton still won't match its lot association.",
         "Backfilled the labels for {lot}, but that didn't fix it -- one carton still won't match its lot association.",
         "The temporary label backfill for {lot} didn't solve it -- one carton still doesn't match.",
         "The label backfill on {lot} didn't work, one carton still doesn't match."],
        ["Re-scanned and corrected {lot}'s lot association; waiting on inventory count confirmation.",
         "Re-scanned everything and fixed {lot}'s lot association, waiting on the count to confirm.",
         "Re-scanned and corrected {lot}'s lot association, just need the inventory count to confirm.",
         "{lot}'s lot association has been corrected, re-scan done, waiting on the count."],
        ['Inventory count confirmed all 12 cartons of {lot} match their lot association; task {task} can be closed.',
         "Count's done for {lot}, all 12 cartons match, {task} can be closed, right?",
         "Inventory count confirms {lot}'s 12 cartons are all associated correctly, {task} can be closed out.",
         'All 12 cartons of {lot} matched in the count, task {task} can be closed.'],
    ],
}

# --- Agent diagnostic-episode vocabulary (phases 0, 2, 3, 4) ---------------

# per-domain tool_name vocabulary an Agent can invoke via agent_tool_call
TOOL_NAMES = {
    'maintenance': ['temperature_query', 'vibration_query', 'coolant_level_query', 'lubrication_pressure_query', 'alarm_log_query'],
    'quality': ['void_rate_query', 'preheat_temp_query', 'material_batch_query', 'inspection_history_query',
                # v0.5d: flux specific gravity is a wave-soldering measurement and solder paste is not an
                # oven attribute -- the oven reports its nitrogen O2 level, paste records hang off the lot
                'paste_trace_query', 'conveyor_speed_query', 'reflow_o2_query'],
    'process': ['yield_query', 'parameter_window_query', 'material_match_query', 'first_article_query',
                'process_capability_query', 'equipment_status_query', 'maintenance_record_query', 'environment_query'],
    'logistics': ['traceability_query', 'label_scan_query', 'inventory_query', 'batch_association_query',
                  'wms_location_query', 'transport_record_query', 'receiving_record_query'],
}
# v0.4: tools that measure the scenario's open problem never appear as a routine "normal"
# check (see generate_dataset.py)
PROBLEM_TOOLS = {
    'maintenance': {'alarm_log_query'},
    'quality': {'void_rate_query', 'preheat_temp_query', 'inspection_history_query'},
    'process': {'yield_query', 'parameter_window_query', 'material_match_query', 'first_article_query',
                'process_capability_query'},
    'logistics': {'traceability_query', 'label_scan_query', 'inventory_query', 'batch_association_query'},
}
TOOL_LABEL = {
    'temperature_query': 'temperature', 'vibration_query': 'vibration', 'coolant_level_query': 'coolant level',
    'lubrication_pressure_query': 'lubrication pressure', 'alarm_log_query': 'alarm log',
    'void_rate_query': 'void rate', 'preheat_temp_query': 'preheat temperature',
    'material_batch_query': 'material batch info', 'inspection_history_query': 'inspection history',
    'paste_trace_query': 'solder paste records', 'conveyor_speed_query': 'conveyor speed',
    'reflow_o2_query': 'nitrogen O2 level',
    'yield_query': 'yield', 'parameter_window_query': 'parameter window', 'material_match_query': 'material match',
    'first_article_query': 'first article', 'process_capability_query': 'process capability',
    'equipment_status_query': 'equipment status', 'maintenance_record_query': 'maintenance records',
    'environment_query': 'shop-floor temperature and humidity',
    'traceability_query': 'traceability record', 'label_scan_query': 'label scan', 'inventory_query': 'inventory count',
    'batch_association_query': 'batch association', 'wms_location_query': 'storage location',
    'transport_record_query': 'transport handover records', 'receiving_record_query': 'receiving records',
}
ROUTINE_VALUE_RANGE = {
    'temperature_query': (55, 68), 'vibration_query': (0.8, 1.8), 'coolant_level_query': (70, 95),
    'lubrication_pressure_query': (0.35, 0.55), 'conveyor_speed_query': (1.0, 1.4), 'reflow_o2_query': (350, 750),
    'environment_query': (22, 26),
}

# routine ("no anomaly") returns -- low density, one template per tool_name so the
# gate has to distinguish tool identity, not just guess from boilerplate wording.
AGENT_ROUTINE = {
    'maintenance': {
        'temperature_query': 'temperature_query returned: {eq} spindle temperature {v}°C, within normal range (≤75°C).',
        'vibration_query': 'vibration_query returned: {eq} vibration at {v}mm/s, below the 2.5mm/s alarm threshold, normal.',
        'coolant_level_query': 'coolant_level_query returned: {eq} coolant level at {v}%, within normal range (60%-100%).',
        'lubrication_pressure_query': 'lubrication_pressure_query returned: {eq} lubrication pressure {v}MPa, within normal range (0.3-0.6MPa).',
    },
    'quality': {
        'material_batch_query': 'material_batch_query returned: {lot} material batch info checks out, no anomaly flags.',
        'paste_trace_query': 'paste_trace_query returned: the solder paste lot printed on {lot} was within shelf life, thaw log normal.',
        'conveyor_speed_query': 'conveyor_speed_query returned: {eq} conveyor speed {v}m/min, within the set range (1.0-1.4m/min).',
        'reflow_o2_query': 'reflow_o2_query returned: {eq} nitrogen atmosphere O2 at {v}ppm, below the 1000ppm limit.',
    },
    'process': {
        'equipment_status_query': 'equipment_status_query returned: {eq} running normally, no downtime logged in the past 24 hours.',
        'maintenance_record_query': "maintenance_record_query returned: {eq}'s recent maintenance was completed on schedule, nothing outstanding.",
        'environment_query': 'environment_query returned: shop floor around {eq} at {v}°C, temperature and humidity within limits.',
    },
    'logistics': {
        'wms_location_query': "wms_location_query returned: {lot}'s storage location matches the WMS record.",
        'transport_record_query': "transport_record_query returned: {lot}'s transport handover records are complete, no damage logged.",
        'receiving_record_query': "receiving_record_query returned: {lot}'s received carton count matches the delivery note, 12 cartons.",
    },
}

# the single high-density tool call per phase -- carries the precise datum
# (tool_name, template) that the Agent's final response is actually grounded in.
# Maintenance phases 2-4 come from MAINT_FAMILY. v0.4: {ts} is when the quoted measurement
# was taken (the reported occurrence, or shortly before the verification session).
AGENT_INFORMATIVE = {
    'maintenance': {
        0: ('alarm_log_query', 'alarm_log_query returned: {eq} tripped alarm code {alarm} ({symptom}), triggered at {ts}, still in alarm state.'),
    },
    'quality': {
        0: ('void_rate_query', 'void_rate_query returned: {lot} void rate measured at 4.8%, over the 3.0% limit, failing, detected at {ts}.'),
        2: ('void_rate_query', "void_rate_query returned: after raising the preheat setpoint, the spot-check void rate on the rework of {lot} climbed back to 4.2%, still over the 3.0% limit -- the temporary adjustment didn't work, detected at {ts}."),
        3: ('void_rate_query', 'void_rate_query returned: after replacing the {comp} and recalibrating the preheat zone, the re-check void rate on the rework of {lot} is 1.8%, below the 3.0% limit, passing, checked at {ts}.'),
        4: ('inspection_history_query', "inspection_history_query returned: {lot}'s 3 pre-release re-checks measured 1.6%, 1.9% and 1.7% void rate, all below the 3.0% limit -- verification passed, checked at {ts}."),
    },
    'process': {
        0: ('yield_query', 'yield_query returned: {eq} current yield 68.5%, below the 85% target, flagged as anomalous, detected at {ts}.'),
        2: ('yield_query', "yield_query returned: after the preheat temperature increase, {eq} yield briefly recovered to 82%, then fell back to 70.2% -- the adjustment didn't hold, detected at {ts}."),
        3: ('first_article_query', "first_article_query returned: after the recipe update, all dimensional and visual inspection items on {eq}'s first article passed -- first article confirmed, checked at {ts}."),
        4: ('process_capability_query', 'process_capability_query returned: {eq} key-dimension Cpk over the 5 consecutive batches since the recipe update is 1.52, above the required 1.33, with no out-of-control points on the control chart and every batch above 85% yield -- stability verification passed, checked at {ts}.'),
    },
    'logistics': {
        0: ('traceability_query', "traceability_query returned: {lot} is missing 2 key traceability nodes (receiving scan, lot association) -- the trace chain can't be completed, detected at {ts}."),
        2: ('inventory_query', "inventory_query returned: after the label backfill, the count shows 1 of {lot}'s 12 cartons still won't match its lot association -- the backfill didn't resolve it, detected at {ts}."),
        3: ('batch_association_query', "batch_association_query returned: after re-scanning, {lot}'s lot association is corrected, 12 of 12 cartons associated, checked at {ts}."),
        4: ('inventory_query', "inventory_query returned: the count confirms all 12 cartons of {lot} match their lot association (including the 1 carton that didn't before), checked at {ts}."),
    },
}

# v0.4: no "what's causing it?" suffix -- the Agent's conclusion reports the finding
# v0.5: no "current data" -- the decisive record is the measurement taken when the event happened
AGENT_QUERY_SUFFIX = ["Can you confirm what's going on?", 'Mind verifying that for me?',
                       'Can you check the related records?', 'Can you take a look?']
AGENT_ACK_SHORT = ['Got it, checking now.', 'Sure, give me a second to pull the data.',
                    'Understood, verifying right away.', 'Okay, let me look into it.']
AGENT_THINK_MID = ["Everything I've checked so far is normal, let me confirm {last_label} too.",
                    "Nothing off with the routine checks yet, taking a look at {last_label} next.",
                    "Everything's in range so far, still need to confirm {last_label}."]
AGENT_THINK_MID_ONE = ["That one's normal, let me confirm {last_label} too.",
                        'Nothing off with that check, taking a look at {last_label} next.']  # v0.5: one routine check
AGENT_FILLER_THINK = ["That one's fine, moving to the next.", 'No issue there, checking the next item.',
                       'Ruled that out, continuing down the list.']
# v0.4: question/answer pairs asked *after* the conclusion
# v0.5: no "no floor action needed" while an alarm is open, and no claim of a write-back the Agent never made
AGENT_FOLLOWUP = [('Anything we should watch for going forward?', "I'll keep watching the related data and flag any change right away."),
                  ('Should this conclusion go into the work order?', "Yes, I'll add it to {task}'s handling record."),
                  ('How will we hear about new data?', "I'll post any new readings here as soon as they come in.")]

# v0.4: phase-1 status broadcasts no longer claim the equipment is "normal" while the
# problem is still open
# v0.5: pushed notifications (nobody queried them), labelled as such
TOOL_ROUTINE = [
    'System push: the inspection plan for {eq} is running on schedule.',
    'System push: task {task} is logged in MES, status in progress.',
    "System push: {eq}'s energy data has been synced to EMS.",
    "System push: nothing new in {lot}'s records this shift.",
    'System push: other equipment in this area passed its patrol inspection.',
    'System push: no new alarms this shift.',
]

# v0.5: openers that start the conversation (see generate_dataset.py)
SMALLTALK_OPEN = ['Good timing, let me catch you up.', 'Morning, everything okay?', 'Just started my shift, anything to hand off?',
                   'Got a minute to sync on progress?']
# acknowledgements in the colleague discussion: no claim that something was already arranged
COLLAB_ACK_POOL = ['Alright, noted.', 'Got it, staying in sync.', 'Noted, will keep watching.', "Okay, let's talk if anything changes."]

# (text, resolution_kind, entity_key) -- resolution_kind in {'time_prev','time_recent','entity'}
CONTEXT_TURNS = [
    ("How's {eq} doing now -- the thing from the shift handoff?", 'shift_prev', 'eq'),
    ("That thing you mentioned earlier, how's it coming along?", 'time_recent', None),
    ('Any other issues with this lot?', 'entity', 'lot'),
    ('Is this machine stable now?', 'entity', 'eq'),
    ('Is it reading normal right now?', 'entity', 'eq'),
    ('Does this batch still need follow-up?', 'entity', 'lot'),
]

# same resolution_kind vocabulary as CONTEXT_TURNS, but worded as a colon lead-in that
# welds into ONE flowing user_query sentence -- "About that thing I flagged last shift:
# {dense report}, {suffix}?" -- mirroring the Chinese generator's comma-splice design,
# rather than three disjointed sentences bolted together with spaces. A colon (not an
# em dash) so it never stacks with a dense report that already contains its own
# internal " -- " aside.
AGENT_QUERY_PREFIX = [
    ('About that thing I flagged last time:', 'time_prev', 'eq'),
    ('About what I just mentioned:', 'time_recent', None),
    ("Wondering how it's doing now:", 'entity', 'eq'),
    ("This lot shouldn't have any other issues, right:", 'entity', 'lot'),
    ("Checking if this machine's stable now:", 'entity', 'eq'),
    ('This batch still needs follow-up, right:', 'entity', 'lot'),
]

# agent_think turns that anaphorically reference an earlier report/entity instead of
# restating it, with the same resolved_time/resolved_entities ground-truth convention.
AGENT_THINK_CONTEXT = [
    ('Building on what was mentioned earlier, let me pull the latest data on {eq}.', 'entity', 'eq'),
    ("That batch's issue from earlier hasn't fully cleared -- let me check the related records.", 'entity', 'lot'),
    ('Following up on what came up at the shift handoff, let me verify that too.', 'shift_prev', 'eq'),
    ("Tying this back to that earlier anomaly, let me check if it's still an issue.", 'time_recent', 'eq'),
]

# shift anchor opening the user_query of phases 0/2/3: when did the reported event
# happen, relative to the shift the session runs in ('cur' = this shift, 'prev' = the
# shift before it, looked up in the site's production calendar). Colon lead-ins, same
# construction as AGENT_QUERY_PREFIX.
SHIFT_OCC_PREFIX = {
    'cur': ['Earlier this shift:', 'At some point this shift:', 'Sometime this shift:'],  # v0.4: no 'shortly after start'
    'prev': ['Per the handoff from the last shift:', 'Heard from the previous crew at shift change:',
             'During the previous shift:'],
}

# cross-turn anaphora: a low-density opener names the subject, then the dense report
# refers to it only by an anaphor, so the report is uninterpretable on its own.
ANAPHOR_OPENER = ['Just got back from checking {subject} on the floor.', 'Quick update on {subject}.',
                  'This one is about {subject}.']  # v0.5: no opener that reads as a request
ANAPHOR = {'eq': ['this machine', 'it'], 'lot': ['this lot', 'this batch']}
ANAPHOR_POSSESSIVE = {'this machine': "this machine's", 'it': 'its', 'this lot': "this lot's",
                      'this batch': "this batch's"}

SHIFT_QUESTION_VARIANTS = {
    0: ['Which shift did the original issue with {subject} happen in?',
        'In which shift did the anomaly on {subject} first show up?',
        'On which day and shift did the problem with {subject} first appear?'],
    # v0.5: when the failed measure was found not to work (it never "held" in every domain)
    # v0.5d: names the unsuccessful corrective measure, not "the first measure" -- in quality scenarios
    # the first measure is the quarantine (see CONTAINMENT), which did not fail
    2: ["In which shift was it found that {subject}'s unsuccessful corrective measure had not worked?",
        'On which day and shift was the reading taken that showed the failed corrective measure on {subject} had not worked?',
        "In which shift was the result measured that showed {subject}'s unsuccessful corrective measure had failed?"],
    # v0.5: when the reading that showed the improvement was taken ("first check" also matched the routine
    # checks, "confirmed working" the final verification)
    # v0.5d: bounded before the final verification, which is also a normal reading after the effective measure
    3: ['After the effective measure on {subject}, in which shift was the reading taken that showed it had improved, before the final verification?',
        'Before the final verification, on which day and shift was the reading taken that showed {subject} back to normal after the effective measure?',
        "Before the final verification, in which shift was the check taken that showed {subject}'s effective measure was working?"],
}

# unanswerable questions -- the history never contains the answer
UNSEEN_ENTITY_CATS = ['single_hop_fact', 'state_tracking', 'failed_intervention']  # templates reused with a look-alike ID
UNRECORDED_ATTRIBUTE_QUESTIONS = ['Which supplier provided the materials or spare parts for {subject}?',
                                  'How much did handling this issue with {subject} cost in total?',
                                  'Which customer complained about the issue with {subject}?']
# v0.5: events that never happened (see generate_dataset.py)
FALSE_PREMISE_QUESTIONS = ['What exactly was the second failed corrective action on {subject}?',  # v0.5: 'measure' read as a measurement
                           'Who was responsible for the second line stop on {subject}?',
                           'In which shift was the second faulty part on {subject} replaced?']  # v0.4: lots are now reworked once
NO_INFO_ANSWER = 'There is no record of this in the history, so it cannot be answered.'
# measures taken on the subject before the failed fix (conditioned_parameter gold), per domain
CONTAINMENT = {'quality': 'raising a quarantine (containment); '}

# Task-level constraints and the open item are stated in the dialogue (phase 3 once the
# fix awaits verification, phase 4 after closing), so constraint_recall and
# working_memory_snapshot are answerable from the history. In v0.1 they existed only in
# working_memory.jsonl, with raw EQ_xxxx ids the dialogue never shows.
CONSTRAINT_TEMPLATES = ['Task {task} cannot be closed until {subject} verification is complete',
                        'Must not reuse an adjustment that has already failed']
CONSTRAINT_JOIN = '; '
CONSTRAINT_NOTE = 'Two constraints to keep in mind: {constraints}.'
# v0.5: a follow-up after closing (see generate_dataset.py)
PENDING_TEMPLATE = "adding this failure mode to {subject}'s inspection SOP"  # v0.5: a lessons-learned item, not blocking closure
PENDING_NOTE = "One open item that doesn't block closing: {pending} -- leaving it open for now."

EXTRA_QA = [
    ('Do we need to stop the line for this?', "Not yet, let's keep watching it."),
    ('Should we escalate this?', 'Let\'s log it for now and see how it develops.'),
    ('Should we notify the other shift?', "Not yet, we've got it covered."),
]

ACK_POOL = ["Got it, I'm on it.", 'Understood, already arranged.', 'Noted, will keep watching.',
            'Okay, staying on top of it.', 'Got it, staying in sync.',
            'Alright, noted.', "Sure, I'll take it from here.", 'Got it, scheduled.']
# v0.4: "thanks for the heads up" only answers an actual reminder (the constraints / open item note)
NOTE_ACK_POOL = ['Thanks for the heads up.', "Got it, I'll keep that in mind.", 'Okay, noted down.']
SMALLTALK_CLOSE = ["Okay, that's it for now -- I'll update if anything changes.", "Alright, I'll keep watching this.",
                    'Yep, staying on it.', "That's it for now, more updates later.", 'Okay, thanks for the work.']

# domain-neutral wording keyed by {subject} (the entity the narrative revolves
# around per domain -- equipment for maintenance/process, material lot for
# quality/logistics; see SUBJECT_OF_DOMAIN) so no template presumes a
# maintenance-only concept like "alarm code" or "component replacement".
QUESTION_VARIANTS = {
    'single_hop_fact': ['What was the earliest issue recorded for {subject}?', 'What anomaly first showed up for {subject}?',
                         'Regarding {subject}, what was the earliest recorded situation?', 'What was the first issue recorded for {subject}?',
                         "Can you tell me about {subject}'s earliest recorded anomaly?", 'What was the earliest problem observed for {subject}?'],
    # v0.4: every wording names the two measures being ordered (the failed one and the effective one)
    'temporal_order': ['For {subject}, which happened first: the measure that did not work or the one that eventually worked?',
                        'In the handling of {subject}, did the failed measure happen before or after the one that eventually worked?',
                        'For this issue with {subject}, what was the order of the failed measure and the effective one?',
                        'Please state the chronological order of the failed intervention and the effective intervention for {subject}.',
                        'In the handling process for {subject}, which came first: the failed measure or the effective one?',
                        'Regarding {subject}, was the measure that failed taken before or after the one that worked?'],
    # v0.5: the last verified status of the subject itself (see generate_dataset.py)
    'state_tracking': ['As of {as_of}, what was the conclusion of the last verification of {subject}?', 'What did the most recent verification of {subject} conclude?',
                        'As of {as_of}, what was the result of the last verification of {subject}?', 'What is the conclusion of the latest verification of {subject}?',
                        'As of now, what did the last verification of {subject} conclude?', 'What state was {subject} in at its last verification?'],  # v0.5
    'failed_intervention': ['Which intervention for {subject} failed and led to a recurrence?', "In handling {subject}, which measure didn't work?",
                             'What was the approach that was tried but failed for {subject}?', 'Which adjustment for {subject} later turned out to be ineffective?',
                             'In resolving the issue with {subject}, which step failed?', 'What was the intervention that previously failed for {subject}?'],
    # v0.3: role-neutral (see generate_dataset.py)
    # v0.5: asks for the two roles only, and the gold names only the roles
    'cross_role_dependency': ['Which role first reported the issue with {subject}, and which role asked for the close-out confirmation at the end?',
                               'Regarding {subject}, which role first reported the problem, and which role asked for the final confirmation?',
                               'In this event involving {subject}, what role reported the problem first, and what role requested the final confirmation?',
                               'For {subject}, what roles did the person who first reported the problem and the one who asked for the final confirmation have?',
                               'In handling {subject}, what roles were at the two ends: reporting the problem and requesting the close-out?',
                               'Regarding {subject}, which role raised the problem first, and which role asked for it to be confirmed closed?'],
    # v0.3: latest state plus open item (see generate_dataset.py)
    'working_memory_snapshot': ["As of {as_of}, what are task {task}'s latest state and open items?",
                                 "As of {as_of}, what's in {task}'s working memory (latest state and open items)?",
                                 "What are {task}'s latest state and unfinished items?", "As of {as_of}, what is {task}'s latest state, and what is still open?",
                                 "What does {task}'s current working-memory snapshot contain (latest state and open items)?",
                                 "As of now, what are {task}'s latest state and remaining open items?"],
    # v0.3: the constraints registered during the task (see generate_dataset.py)
    # v0.5: only asks for the registered constraints
    'constraint_recall': ['Which constraints were registered while task {task} was being handled?',
                           'Which constraints was {task} required to follow during its handling?',
                           'Regarding {task}, which constraints were explicitly raised on site?',
                           "Which rules that must not be violated were set during {task}'s handling?",
                           'Which constraints was someone reminded of while {task} was being handled?',
                           'What constraints were registered during {task}?'],
    # v0.3: the superseded measure (see generate_dataset.py)
    # v0.4: asks about the *failed* measure -- the "first measure" is the quarantine in quality scenarios
    'superseded_fact': ['Which measure for {subject} did not work, and what replaced it?',
                         "Regarding {subject}, when the failed fix didn't work, what was it changed to?",
                         'What were the failed approach and the later effective approach for {subject}?',
                         'Which failed measure for {subject} was later superseded by a new one?',
                         'Regarding {subject}, which failed measure did the effective one replace?',
                         'How did the remediation plan for {subject} change after the unsuccessful corrective measure?'],  # v0.5d: not "first fix"
    # v0.4: bounded at the verification, so the open archiving item is out of scope
    'multi_hop_event': ['Please walk through the full process for {subject} from the issue appearing to passing verification, in order.',
                         'What stages did this issue with {subject} go through, from discovery to passing verification?',
                         'Can you walk me through the handling of {subject} in order, from the first report to passing verification?',
                         'What steps did {subject} go through from the issue appearing until the task could be closed?',
                         'Please summarize the handling chain for the issue with {subject}, from discovery to passing verification.',
                         "What's the full story of this event involving {subject}, from discovery to passing verification?"],
    # v0.5: asks for the measures in order, including the one that failed (the gold marks which worked)
    'conditioned_parameter': ['Which measures were taken for {subject}, in order, including any that did not work?',
                               'Which measures were tried on {subject} in turn, and how did each work out?',
                               'Regarding {subject}, which measures were tried, in order?',
                               'Which measures were taken for {subject}, in order, and after which one did verification pass?',
                               'What measures did {subject} go through while its issue was handled, in order?',
                               'In order, which measures were applied to {subject}?'],  # v0.5: no {task}
}

# tool_evidence_recall is asked once per Agent episode (phases 0/2/3/4), each with its own
# distinct correct answer. A phase-agnostic wording like "What evidence did the Agent use to
# reach its conclusion?" would be genuinely ambiguous -- the same question text would map to
# 4 different gold answers within one scenario, with nothing in the text to tell them apart.
# Each phase therefore gets its own narrative-stage-specific wording (domain-neutral via
# {subject}, same principle as QUESTION_VARIANTS) so the question text itself disambiguates
# which of the 4 diagnoses it's asking about.
TOOL_EVIDENCE_QUESTION_VARIANTS = {
    0: ['When the issue was first reported for {subject}, what detection data did the Agent use to reach its conclusion?',
        'At the initial report stage for {subject}, what specific evidence did the Agent cite?',
        'When the anomaly was first flagged for {subject}, which detection result did the Agent rely on?'],
    # v0.5d: the unsuccessful corrective measure, not "the first fix" (the quarantine in quality scenarios)
    2: ['When the unsuccessful corrective measure on {subject} turned out not to hold, what detection data did the Agent cite?',
        'At the point where the failed corrective measure on {subject} was found not to work, what evidence did the Agent use?',
        "When it became clear the unsuccessful corrective measure on {subject} hadn't worked, what specific data did the Agent rely on?"],
    # v0.4: phase 3 only shows the fix working (verification is still pending)
    # v0.5: the reading that showed the improvement ("first check" also matched the routine checks)
    # v0.5d: bounded before the final verification, which also reads normal after the effective measure
    3: ['After the effective measure on {subject}, what data did the reading that showed the improvement give, before the final verification?',
        'Before the final verification, which detection data did the Agent rely on to judge that {subject} had improved after the effective measure?',
        "Before the final verification, what were the specific readings of the check that showed {subject}'s effective measure was working?"],
    4: ['During the final verification for {subject}, what key detection data did the Agent base its conclusion on?',
        'Before closing out the task for {subject}, which key check result did the Agent rely on to confirm it was resolved?',
        "At the final verification stage for {subject}, what key data supported the Agent's conclusion?"],
}

# machine-tool-style display names for dialogue text only (structured fields keep EQ_xxxx/LOT_xxxx)
# -- already alphanumeric model-number style, identical in both language editions.
DISPLAY_EQ = {
    'maintenance': ['CA6140-M1', 'Z3040-M2', 'X6132-M3', 'T68-M4', 'CA6136-M5', 'MJ1640-M6'],
    'quality': ['SMT-REFLOW-01', 'REFLOW-OVEN-02', 'SMT-REFLOW-03', 'N2-REFLOW-04'],  # v0.5: reflow ovens only
    'process': ['INJ-MOLD-05', 'CNC-MC500-06', 'PRESS-LINE-07', 'EXTRUDE-08'],
    'logistics': ['CONV-LINE-09', 'AS-RS-10', 'PALLET-STA-11', 'SORT-HUB-12'],
}
# component/part display names for dialogue text only (structured fields keep
# COMP_SENSOR_xxxx) -- only maintenance and logistics templates actually reference
# {comp}, but a name is defined for every domain for symmetry with DISPLAY_EQ. Bare
# noun phrases (no article baked in) since templates supply "the"/"a" as needed.
DISPLAY_COMP = {
    'maintenance': ['temperature sensor', 'vibration sensor', 'coolant sensor', 'spindle bearing', 'drive belt'],
    'quality': ['reflow sensor', 'preheat sensor'],
    'process': ['pressure sensor', 'feed sensor'],
    'logistics': ['shipping carton', 'sub-lot tag', 'pallet label'],
}


def fmt(tmpl, **kw):
    return tmpl.format(**kw)


def lower_first(s):
    return s[:1].lower() + s[1:] if s else s


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
    """Replace the subject's display name in a dense report with an anaphor (possessive-aware)."""
    ana = rng.choice(ANAPHOR[subject_key])
    out = report.replace(f"{subject_text}'s", ANAPHOR_POSSESSIVE[ana]).replace(subject_text, ana)
    return out[:1].upper() + out[1:]


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
    return int(v) if tool_name == 'reflow_o2_query' else round(v, 1)


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
    # Phases 0/2/3 always anchor the event to a shift ("Per the handoff from the last
    # shift: ...") with resolved_time/resolved_shift ground truth -- shift_temporal
    # questions ask about it. Phase 4 keeps the ~30% relative-time / vague-referent
    # lead-in ("About that thing I flagged last time: ..."). When a lead-in is used, the
    # whole thing is welded into ONE flowing sentence (lead-in: report, tag question?)
    # rather than three sentences bolted together with spaces -- mirroring the Chinese
    # generator's comma-splice construction.
    subject_key = SUBJECT_OF_DOMAIN[domain]
    subject_text = dctx[subject_key]
    dense_text = fmt(rng.choice(PARAPHRASE[domain][phase]), **dctx)
    suffix = rng.choice(AGENT_QUERY_SUFFIX)
    ptext, uq_extra, occurrence = '', {}, None
    if phase in SHIFT_PHASES:
        when = rng.choice(['cur', 'prev'])
        occ = pick_occurrence(clock, when)
        occurrence = {'time': occ, 'shift': shift_of(occ, clock['cal']), 'when': when}
        ptext = rng.choice(SHIFT_OCC_PREFIX[when])
        uq_extra = {'resolved_time': occ.isoformat(), 'resolved_shift': occurrence['shift'][0]}
    elif rng.random() < 0.3:
        ptext, kind, key = rng.choice(on_topic(AGENT_QUERY_PREFIX, subject_key))
        uq_extra = resolve_extra(kind, key, eq, lot, clock) or {}

    # cross-turn anaphora: a low-density opener names the subject and the dense report
    # only says "this machine/it/this lot", so the report cannot be interpreted on its own.
    if phase >= 2 and subject_text in dense_text and not (phase == 4 and ptext) and rng.random() < 0.4:
        add(reporter_id, reporter_role, fmt(rng.choice(ANAPHOR_OPENER), subject=subject_text), 'human', True)
        dense_text = anaphorize(dense_text, subject_text, subject_key)
        uq_extra['resolved_entities'] = sorted(set(uq_extra.get('resolved_entities', [])) |
                                               {eq if subject_key == 'eq' else lot})
    if ptext:
        uq_text = f'{ptext} {strip_period(dense_text)}, {lower_first(suffix)}'
    else:
        uq_text = f'{dense_text} {suffix}'
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
    those = 'those look' if len(chosen_routine) > 1 else 'that looks'
    plan_text = f"I'll check { ', '.join(TOOL_LABEL[t] for t in chosen_routine) } first; if {those} normal, I'll confirm {TOOL_LABEL[key_tool]} next."
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
    # measurement time is when the reported event happened (phases 0/2/3) or shortly before the
    # verification session (phase 4), never after the user already reported the result.
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
    # question/answer exchange after the conclusion but before the user signs off
    # v0.4: the Agent's reply is spoken to the user, so it is an agent_response (low density)
    if rng.random() < 0.3:
        fq, fa = rng.choice(AGENT_FOLLOWUP)
        add(other_id, other_role, fq, 'human', True)
        add(agent_id, 'industrial_agent', fmt(fa, **dctx), 'agent_response', True)

    # 6. user_ack -- low density. v0.4: after the other engineer's note, the reporter acknowledges it.
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
    """The observation's one-line `message`: a v0.4-style tool sentence without its '<tool>returned: ' prefix."""
    msg = text.split('returned: ', 1)[1]
    return msg[:1].upper() + msg[1:]


def strip_period(s):
    return s[:-1] if s.endswith('.') else s


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
    fix = bare[3].split(';')[0]  # the effective fix as an action, without its transient state (v0.3)
    if cat == 'single_hop_fact':
        ref = canon[0]
    elif cat == 'temporal_order':
        # phase2 is always the failed/temporary measure and phase3 the one that
        # eventually works, but *what kind* of measure that is differs per domain
        # (parameter tweak, threshold relaxation, label rescan, ...), so the
        # labels here stay generic instead of naming a specific action.
        # v0.5d: "the failed measure", not "the first measure" (the quarantine comes first in quality scenarios)
        ref = f'The failed measure ({bare[2]}) happened first, followed by the one that eventually worked ({fix}).'
    elif cat == 'state_tracking':
        ref = canon[4]
    elif cat == 'failed_intervention':
        ref = canon[2]  # v0.4: only the failed measure (see generate_dataset.py)
    elif cat == 'cross_role_dependency':
        owner = ROLE_NAMES[dict(ROLES)[DOMAIN_OWNER[domain]]]
        ref = f'The {owner} reported the problem first, and the {owner} also asked for the final close-out confirmation.'
    elif cat == 'working_memory_snapshot':
        return {'type': 'working_memory', 'active_memory_ids': memids[4:5],
                'pending_item_ids': [], 'constraints': [], 'aux': aux}
    elif cat == 'constraint_recall':
        ref = CONSTRAINT_JOIN.join(constraints)
    elif cat == 'superseded_fact':
        ref = f'The measure that did not work was "{bare[2]}"; it was replaced by "{fix}", which later passed verification.'
    elif cat == 'multi_hop_event':
        ref = '; '.join(bare) + '.'
    elif cat == 'conditioned_parameter':
        ref = (f'Measures taken for {subject}, in order: {CONTAINMENT.get(domain, "")}{bare[2]} (did not work); '
               f'then {fix} (verification passed after it).')
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
        # v0.4: the five sessions span about two weeks (see generate_dataset.py)
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
        # dialogue text (e.g. "Initial inspection points to COMP_SENSOR_0001") -- give
        # it the same cosmetic display-name treatment already applied to eq/lot.
        comp_name = DISPLAY_COMP[domain][(i - 1) % len(DISPLAY_COMP[domain])]
        disp_comp = f'{comp_name} #{serial:03d}'
        dctx = {'eq': disp_eq, 'comp': disp_comp, 'lot': disp_lot, 'alarm': alarm, 'task': task, 'family': comp_name}
        if domain == 'maintenance':
            measure = fmt(MAINT_FAMILY[comp_name]['measure'], **dctx)
            dctx.update(symptom=MAINT_FAMILY[comp_name]['symptom'], measure=measure, measure_lc=lower_first(measure))
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
