"""Structured tool calls and observations shared by the ZH and EN generators (v0.5).

An Agent tool use is two turns: the Agent's call (turn_type agent_tool_call, a JSON request
with tool name and arguments) and the plant system's reply (turn_type tool_observation, a
JSON record as an EMS/MES/QMS/WMS API would return it). Field names and values are
language-neutral, so both language editions share this module; only the `message` field
(a one-line natural-language summary the system attaches) differs per language and is passed
in by the generator. The decisive observation of an episode carries the scenario's key
datum both as structured fields and in `message`, which is also the tool_evidence_recall gold.

Maintenance key readings are keyed by fault family, in the same order as
DISPLAY_COMP['maintenance'] in both generators.
"""
import json

MAINT_FAMILIES = ['temp_sensor', 'vib_sensor', 'coolant_sensor', 'bearing', 'belt']
ALARM_CATEGORY = {'temp_sensor': 'SPINDLE_OVERTEMP', 'coolant_sensor': 'COOLANT_LEVEL_LOW',
                  'vib_sensor': 'SPINDLE_VIBRATION_HIGH', 'bearing': 'SPINDLE_VIBRATION_HIGH',
                  'belt': 'VIBRATION_HIGH_ABNORMAL_NOISE'}

# tools whose subject is the material lot (the rest query the equipment)
LOT_TOOLS = {'void_rate_query', 'material_batch_query', 'inspection_history_query', 'traceability_query',
             'label_scan_query', 'inventory_query', 'batch_association_query', 'wms_location_query',
             'transport_record_query', 'receiving_record_query', 'paste_trace_query'}

TEMP_LIMITS = {'spindle_temp_c_max': 75}
VIB_LIMITS = {'alarm_threshold_mm_s': 2.5}
COOLANT_LIMITS = {'level_pct_min': 60, 'level_pct_max': 100}
VOID_LIMITS = {'void_rate_pct_max': 3.0}

# (data, limits, result) of the decisive observation, per maintenance family and phase 2/3/4
MAINT_KEY = {
    'temp_sensor': {
        2: ({'spindle_temp_c': 81.0, 'min_after_adjustment_c': 69.4, 'trend': 'rising_after_temporary_drop'},
            TEMP_LIMITS, 'adjustment_ineffective'),
        3: ({'spindle_temp_c': 58.0, 'handheld_reference_c': 57.0, 'sensor_deviation_c': 1.0},
            {**TEMP_LIMITS, 'sensor_deviation_c_max': 3.0}, 'normal'),
        4: ({'window_h': 72, 'spindle_temp_c_min': 56.0, 'spindle_temp_c_max': 60.0, 'exceedance_count': 0},
            TEMP_LIMITS, 'verification_passed')},
    'coolant_sensor': {
        2: ({'coolant_level_pct': 32.0, 'sight_glass_level': 'normal', 'trend': 'dropped_after_top_up'},
            COOLANT_LIMITS, 'adjustment_ineffective'),
        3: ({'coolant_level_pct': 88.0, 'sight_glass_level': 'normal'}, COOLANT_LIMITS, 'normal'),
        4: ({'window_h': 72, 'coolant_level_pct_min': 85.0, 'coolant_level_pct_max': 90.0, 'low_level_alarm_count': 0},
            COOLANT_LIMITS, 'verification_passed')},
    'vib_sensor': {
        2: ({'vibration_rms_mm_s': 4.1, 'handheld_reference_mm_s': 1.3, 'trend': 'jumped_after_temporary_normal'},
            VIB_LIMITS, 'adjustment_ineffective'),
        3: ({'vibration_rms_mm_s': 1.2, 'handheld_reference_mm_s': 1.2}, VIB_LIMITS, 'normal'),
        4: ({'window_h': 72, 'vibration_rms_mm_s_min': 1.1, 'vibration_rms_mm_s_max': 1.3, 'exceedance_count': 0},
            VIB_LIMITS, 'verification_passed')},
    'bearing': {
        2: ({'vibration_rms_mm_s': 3.8, 'min_after_adjustment_mm_s': 2.1, 'trend': 'rising_after_temporary_drop'},
            VIB_LIMITS, 'adjustment_ineffective'),
        3: ({'vibration_rms_mm_s': 1.1}, VIB_LIMITS, 'normal'),
        4: ({'window_h': 72, 'vibration_rms_mm_s_min': 1.0, 'vibration_rms_mm_s_max': 1.3, 'exceedance_count': 0},
            VIB_LIMITS, 'verification_passed')},
    'belt': {
        2: ({'vibration_rms_mm_s': 3.5, 'belt_slip_detected': True, 'trend': 'rising_after_temporary_normal'},
            VIB_LIMITS, 'adjustment_ineffective'),
        3: ({'vibration_rms_mm_s': 1.2, 'abnormal_noise': False}, VIB_LIMITS, 'normal'),
        4: ({'window_h': 72, 'vibration_rms_mm_s_min': 1.1, 'vibration_rms_mm_s_max': 1.4, 'exceedance_count': 0},
            VIB_LIMITS, 'verification_passed')},
}

# (data, limits, result) of the decisive observation for the other domains, per phase
DOMAIN_KEY = {
    'quality': {
        0: ({'void_rate_pct': 4.8, 'sample_size': 32}, VOID_LIMITS, 'fail'),
        2: ({'void_rate_pct': 4.2, 'sample_size': 32, 'context': 'rework_after_preheat_setpoint_raise'},
            VOID_LIMITS, 'adjustment_ineffective'),
        3: ({'void_rate_pct': 1.8, 'sample_size': 32, 'context': 'rework_after_sensor_replacement_and_calibration'},
            VOID_LIMITS, 'pass'),
        4: ({'rechecks': [{'seq': 1, 'void_rate_pct': 1.6}, {'seq': 2, 'void_rate_pct': 1.9},
                          {'seq': 3, 'void_rate_pct': 1.7}]}, VOID_LIMITS, 'verification_passed'),
    },
    'process': {
        0: ({'yield_pct': 68.5, 'units_inspected': 1200}, {'target_yield_pct': 85}, 'abnormal'),
        2: ({'yield_pct_peak_after_adjustment': 82.0, 'yield_pct_current': 70.2}, {'target_yield_pct': 85},
            'adjustment_ineffective'),
        3: ({'inspection_items': {'dimensions': 'pass', 'appearance': 'pass'}, 'first_article_status': 'confirmed'},
            {}, 'pass'),
        4: ({'batches': 5, 'cpk': 1.52, 'min_batch_yield_pct': 86.4, 'control_chart': 'in_control', 'out_of_control_points': 0},
            {'cpk_min': 1.33, 'target_yield_pct': 85},
            'verification_passed'),
    },
    'logistics': {
        0: ({'nodes_total': 10, 'nodes_present': 8, 'missing_nodes': ['receiving_scan', 'lot_association']},
            {}, 'incomplete'),
        2: ({'cartons_total': 12, 'cartons_matched': 11, 'cartons_unmatched': 1}, {}, 'unresolved'),
        3: ({'cartons_total': 12, 'cartons_associated': 12}, {}, 'corrected'),
        4: ({'cartons_total': 12, 'cartons_matched': 12, 'previously_unmatched_now_matched': 1}, {},
            'verification_passed'),
    },
}


def target_of(tool, dctx):
    if tool == 'material_match_query':
        return {'equipment': dctx['eq'], 'lot': dctx['lot']}
    return {'lot': dctx['lot']} if tool in LOT_TOOLS else {'equipment': dctx['eq']}


def call_json(tool, dctx, window):
    """The Agent's request."""
    return json.dumps({'tool': tool, 'args': {**target_of(tool, dctx), 'window': window}}, ensure_ascii=False)


def observation_json(rng, tool, dctx, measured_at, data, limits, result, message):
    """The plant system's reply."""
    return json.dumps({'request_id': f'req-{rng.getrandbits(32):08x}', 'tool': tool, 'status': 'success',
                       'target': target_of(tool, dctx), 'measured_at': f'{measured_at:%Y-%m-%d %H:%M}',
                       'data': data, 'limits': limits, 'result': result, 'data_quality': 'good',
                       'message': message}, ensure_ascii=False)


def routine_data(rng, tool, v, measured_at):
    """(data, limits) of a routine all-normal observation; v is the headline reading."""
    r1 = lambda lo, hi: round(rng.uniform(lo, hi), 1)
    if tool == 'temperature_query':
        return ({'spindle_temp_c': v, 'bearing_temp_c': round(v - rng.uniform(4, 9), 1), 'ambient_temp_c': r1(22, 26)},
                TEMP_LIMITS)
    if tool == 'vibration_query':
        return {'vibration_rms_mm_s': v, 'vibration_peak_mm_s': round(v * rng.uniform(1.4, 1.8), 1)}, VIB_LIMITS
    if tool == 'coolant_level_query':
        return {'coolant_level_pct': v, 'coolant_temp_c': r1(20, 26)}, COOLANT_LIMITS
    if tool == 'lubrication_pressure_query':
        return ({'lube_pressure_mpa': v, 'pump_state': 'running'},
                {'lube_pressure_mpa_min': 0.3, 'lube_pressure_mpa_max': 0.6})
    if tool == 'material_batch_query':
        return {'material_batch_check': 'consistent', 'flags': []}, {}
    if tool == 'solder_paste_query':
        return {'paste_lot': f'SP-{rng.randint(1000, 9999)}', 'within_shelf_life': True, 'thaw_log': 'normal'}, {}
    if tool == 'paste_trace_query':  # v0.5d, EN only: paste records by lot (solder_paste_query is ZH-only)
        return {'paste_lot': f'SP-{rng.randint(1000, 9999)}', 'within_shelf_life': True, 'thaw_log': 'normal'}, {}
    if tool == 'reflow_o2_query':  # v0.5d, EN only: replaces flux_query (a wave-soldering measurement)
        return {'o2_ppm': v}, {'o2_ppm_max': 1000}
    if tool == 'conveyor_speed_query':
        return {'conveyor_speed_m_min': v}, {'conveyor_speed_m_min_min': 1.0, 'conveyor_speed_m_min_max': 1.4}
    if tool == 'flux_query':
        return {'flux_specific_gravity': float(v)}, {'flux_specific_gravity_min': 0.80, 'flux_specific_gravity_max': 0.84}
    if tool == 'equipment_status_query':
        return {'run_state': 'running', 'downtime_min_24h': 0}, {}
    if tool == 'maintenance_record_query':
        last_pm = measured_at.date().fromordinal(measured_at.date().toordinal() - rng.randint(3, 10))
        return {'last_pm_date': last_pm.isoformat(), 'open_items': 0}, {}
    if tool == 'environment_query':
        return ({'shop_temp_c': v, 'humidity_pct': r1(40, 55)},
                {'shop_temp_c_range': [18, 28], 'humidity_pct_range': [30, 60]})
    if tool == 'wms_location_query':
        return {'location': f'A-{rng.randint(1, 9):02d}-{rng.randint(1, 30):02d}', 'wms_match': True}, {}
    if tool == 'transport_record_query':
        return {'handover_complete': True, 'damage_reports': 0}, {}
    if tool == 'receiving_record_query':
        return {'cartons_received': 12, 'delivery_note_cartons': 12}, {}
    raise KeyError(tool)


def key_data(domain, phase, family, dctx, measured_at):
    """(data, limits, result) of the decisive observation."""
    if domain == 'maintenance':
        if phase == 0:
            return ({'active_alarm_count': 1,
                     'alarms': [{'code': dctx['alarm'], 'category': ALARM_CATEGORY[family],
                                 'triggered_at': f'{measured_at:%Y-%m-%d %H:%M}', 'state': 'active'}]},
                    {}, 'alarm_active')
        return MAINT_KEY[family][phase]
    return DOMAIN_KEY[domain][phase]
