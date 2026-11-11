# Auditor calibration, v0.5b EN test90 (model review by Claude, not a human annotator)

Sample: 3 major-flagged + 1 ok per category (seed 0), 52 items. Flagged: 39, reviewer agrees with 14, unsure 1, disagrees 24 -> luna precision on major flags ~36%. All 13 "ok" items confirmed.

| category | luna major | sample agree | est. true major |
|---|---|---|---|
| conditioned_parameter | 113 | 0/3 | 0 |
| constraint_recall | 50 | 0/3 | 0 |
| cross_role_dependency | 124 | 0/3 | 0 |
| failed_intervention | 57 | 2/3 | 38 |
| multi_hop_event | 74 | 0/3 | 0 |
| shift_temporal | 92 | 2/3 | 61 |
| single_hop_fact | 24 | 0/3 | 0 |
| state_tracking | 58 | 1/3 | 19 |
| superseded_fact | 53 | 2/3 | 35 |
| temporal_order | 55 | 0/3 | 0 |
| tool_evidence_recall | 38 | 2/3 | 25 |
| unanswerable | 23 | 3/3 | 23 |
| working_memory_snapshot | 50 | 2/3 | 33 |
| total | 811 (23.1%) | 14/39 | 236 (6.7%) |

Root causes of the agreed issues (all fixed in the v0.5 round-4 wording fixes): logistics phase-2 "helped, but" reading as partial success; the phase-3 shift / tool-evidence wording colliding with the final verification; "last check" including routine checks; EN "measure" read as a measurement in a false premise; the open SOP item not marked non-blocking.

## Per-item verdicts

- 1. conditioned_parameter (flagged, luna gold_incomplete/major): **disagree** -- routine diagnostic checks are not measures tried
- 2. conditioned_parameter (flagged, luna gold_extra/major): **disagree** -- "verification passed after it" is literally true
- 3. conditioned_parameter (flagged, luna gold_extra/major): **disagree** -- misreads "after it" as immediately after
- 4. conditioned_parameter (ok, luna ok/none): **agree** -- auditor ok confirmed on review
- 5. constraint_recall (flagged, luna gold_incomplete/major): **disagree** -- SOP open item is not a constraint
- 6. constraint_recall (flagged, luna gold_incomplete/major): **disagree** -- same
- 7. constraint_recall (flagged, luna gold_incomplete/major): **disagree** -- same
- 8. constraint_recall (ok, luna ok/none): **agree** -- auditor ok confirmed on review
- 9. cross_role_dependency (flagged, luna gold_wrong/major): **disagree** -- auditor confirms the gold is right in its own explanation
- 10. cross_role_dependency (flagged, luna question_ambiguous/major): **disagree** -- "final confirmation" = close-out is clear
- 11. cross_role_dependency (flagged, luna gold_wrong/major): **disagree** -- gold matches the dialogue
- 12. cross_role_dependency (ok, luna ok/none): **agree** -- auditor ok confirmed on review
- 13. failed_intervention (flagged, luna gold_wrong/major): **agree** -- logistics phase-2 wording "helped, but" reads as partial success -> fixed
- 14. failed_intervention (flagged, luna gold_wrong/major): **agree** -- same root cause -> fixed
- 15. failed_intervention (flagged, luna evidence_mismatch/major): **unsure** -- could not verify the evidence-id claim from the sheet
- 16. failed_intervention (ok, luna ok/none): **agree** -- auditor ok confirmed on review
- 17. multi_hop_event (flagged, luna gold_extra/major): **disagree** -- gold keeps the uncertainty ("root cause still unconfirmed")
- 18. multi_hop_event (flagged, luna gold_incomplete/major): **disagree** -- routine checks are not process steps
- 19. multi_hop_event (flagged, luna gold_incomplete/major): **disagree** -- question is bounded at passing verification; the SOP item comes after
- 20. multi_hop_event (ok, luna ok/none): **agree** -- auditor ok confirmed on review
- 21. shift_temporal (flagged, luna gold_wrong/major): **disagree** -- night shift starting 09-17 covers 09-18 06:49; auditor wrong
- 22. shift_temporal (flagged, luna gold_wrong/major): **agree** -- "confirmed to be working" collides with the final verification -> reworded to the first check (regression I introduced)
- 23. shift_temporal (flagged, luna gold_wrong/major): **agree** -- same -> fixed
- 24. shift_temporal (ok, luna ok/none): **agree** -- auditor ok confirmed on review
- 25. single_hop_fact (flagged, luna gold_incomplete/major): **disagree** -- canonical gold without the incidental numbers is fine under the judge rule
- 26. single_hop_fact (flagged, luna gold_incomplete/major): **disagree** -- same
- 27. single_hop_fact (flagged, luna gold_incomplete/major): **disagree** -- same
- 28. single_hop_fact (ok, luna ok/none): **agree** -- auditor ok confirmed on review
- 29. state_tracking (flagged, luna question_ambiguous/major): **agree** -- "last check" includes routine checks -> "last verification"
- 30. state_tracking (flagged, luna gold_extra/major): **disagree** -- SOP item is not part of the verification conclusion
- 31. state_tracking (flagged, luna gold_extra/major): **disagree** -- same
- 32. state_tracking (ok, luna ok/none): **agree** -- auditor ok confirmed on review
- 33. superseded_fact (flagged, luna question_ambiguous/major): **agree** -- same root cause as 13 -> fixed
- 34. superseded_fact (flagged, luna gold_extra/major): **disagree** -- pedantic causality
- 35. superseded_fact (flagged, luna gold_wrong/major): **agree** -- same root cause as 13 -> fixed
- 36. superseded_fact (ok, luna ok/none): **agree** -- auditor ok confirmed on review
- 37. temporal_order (flagged, luna gold_extra/major): **disagree** -- pedantic: the recipe update is followed by the passing verification (now stated in the message)
- 38. temporal_order (flagged, luna question_ambiguous/major): **disagree** -- same
- 39. temporal_order (flagged, luna question_ambiguous/major): **disagree** -- same
- 40. temporal_order (ok, luna ok/none): **agree** -- auditor ok confirmed on review
- 41. tool_evidence_recall (flagged, luna gold_incomplete/major): **disagree** -- routine checks are not the evidence for the conclusion
- 42. tool_evidence_recall (flagged, luna question_ambiguous/major): **agree** -- first-article vs final capability both fit "after the effective measure" -> reworded to the first check
- 43. tool_evidence_recall (flagged, luna question_ambiguous/major): **agree** -- same -> fixed
- 44. tool_evidence_recall (ok, luna ok/none): **agree** -- auditor ok confirmed on review
- 45. unanswerable (flagged, luna label_answerability/major): **agree** -- EN "measure" read as a measurement -> "corrective action"
- 46. unanswerable (flagged, luna label_answerability/major): **agree** -- same -> fixed
- 47. unanswerable (flagged, luna label_answerability/major): **agree** -- same -> fixed
- 48. unanswerable (ok, luna ok/none): **agree** -- auditor ok confirmed on review
- 49. working_memory_snapshot (flagged, luna evidence_mismatch/major): **disagree** -- auditor contradicts itself
- 50. working_memory_snapshot (flagged, luna question_ambiguous/major): **agree** -- "can be closed" next to an open item read as conflicting -> item marked non-blocking
- 51. working_memory_snapshot (flagged, luna gold_wrong/major): **agree** -- same -> fixed
- 52. working_memory_snapshot (ok, luna ok/none): **agree** -- auditor ok confirmed on review