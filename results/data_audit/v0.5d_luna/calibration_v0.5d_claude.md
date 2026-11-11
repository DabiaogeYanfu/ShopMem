# v0.5d targeted luna audit: calibration (model review by Claude, not human), 2026-09-30

Data: EN working tree after the v0.5d fixes (md5s in data_md5.txt; uncommitted at audit time). Targeted: 4 categories
(1080 q) + scenario audit of all 90 scenarios (unchanged scenarios reuse cached v0.5c verdicts).

| bucket | v0.5c major | v0.5d major | sampled flags | real | notes |
|---|---|---|---|---|---|
| tool_evidence_recall | 60 | 31 | 8 (phase 3) | 0 | luna says "before the final verification" could mean the final verification |
| shift_temporal | 72 | 73 | 9 (phase 2/3) | 0 | self-corrections ("Wait, this matches the gold"), night shift across midnight, 17:44 called Night; all shift labels verified programmatically against occurred_at |
| temporal_order | 39 | 44 | 5 | 0 | all process domain: "not established that the recipe update was the effective measure" (S05 record says "since the recipe update") |
| superseded_fact | 31 | 29 | 3 | 0 | "never says the recipe update replaced the failed adjustment" |
| scenario implausible/major | 23 | 17 | all 17 | 0 major | flux/paste-on-oven gone; 3 new: paste_trace_query with a 24h window can't identify an older lot's paste (minor, same for every lot-scoped tool); rest = cached v0.5c FPs |
