# v0.5c luna audit: per-category calibration (model review by Claude, not human), 2026-09-30

Data audited: commit 1e0df27. Round-4 changes vs v0.5b data (d226b2b): 159/5054 turns changed (mostly PENDING_NOTE
"One open item that doesn't block closing"), tool_evidence/shift phase-3 question wording -> "first check", state_tracking -> "verification".

| bucket | luna major | sampled flags | real | precision | verdict |
|---|---|---|---|---|---|
| scenario implausible/major | 23 (11 scen., same count as v0.5b) | all 23 | 7 (flux SG / solder paste attributed to reflow oven) | ~30% | real issue is PRE-EXISTING (flux_query in routine-check pool, 46 calls in EN test90), not a round-4 regression; other 16 = 72h-window misreads, sensor-vs-handheld clue, in-window timestamps |
| tool_evidence_recall phase-3 ("first check") | 57/90 (v0.5b 32) | 8 (+3 ok) | 4 clear + 2 borderline | 50-75% | REAL round-4 regression: routine checks are queried before the relevant reading, so "first check" points at them; other templates 0-2 flags |
| constraint_recall | 60 (v0.5b 50) | 8 (+3 ok) | 0 | 0% | all 60 explanations = SOP follow-up counted as constraint (known luna FP); q+gold byte-identical to v0.5b |
| cross_role_dependency | 121 | 4 (+1 ok) | 0 | 0% | self-contradicting explanations / hallucinated "other events" |
| conditioned_parameter | 98 | 4 (+1 ok) | 0 | 0% | wants "keep watching" / verification / investigation listed as measures |

Uncommitted round-5 draft (working tree 14:45, not audited): rewords shift_temporal ph2/3 + tool_evidence ph3 to
"the reading that showed it had improved". Caveat: phase-2 readings also show a brief recovery ("brought it back briefly").
