# v0.5c luna audit (EN test90, 3510 q / 90 scenarios)
- auditor: gpt-6-luna, evaluation/audit_gold_llm.py, run 2026-09-29 ~14:09-14:43 UTC
- data audited: commit 1e0df27 (data v0.5 round 4b). NOT the working tree regenerated at 14:45 (uncommitted round-5 shift/tool_evidence phase 2-3 wording).
- raw: major 633/3510 (18.0%), scenarios with major issue 32/90. vs v0.5b: 811 (23.1%), 40/90.
- split_parity_*.json (14:46) left in the parent dir: layer 1-3 output, reflects the post-14:45 working tree.
