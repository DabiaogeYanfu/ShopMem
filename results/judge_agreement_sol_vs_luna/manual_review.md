# Manual review: luna CORRECT / sol WRONG on the same trajectory (2026-10-10)

12 cases, stratified over the 6 categories with the most disagreements (2 each, seed 3), read by Claude
against question + gold + prediction.

| category | question | verdict of the review | note |
|---|---|---|---|
| constraint_recall | Q_GEN_0043_33 s2 | borderline | both constraints right; adds "ceased to apply at closure" (from the memory's validity marks) |
| constraint_recall | Q_GEN_0026_07 s1 | borderline | same pattern; sol calls the no-reuse constraint ceasing a contradiction |
| conditioned_parameter | Q_GEN_0007_10 s2 | luna right | lists the stability verification as a 3rd step; true, extra |
| conditioned_parameter | Q_GEN_0068_36 s1 | luna right | same, final inventory verification as a 3rd step |
| failed_intervention | Q_GEN_0046_17 s2 | luna right | right measure; omits "briefly helped", adds true readings |
| failed_intervention | Q_GEN_0025_30 s0 | luna right | right measure + true readings |
| superseded_fact | Q_GEN_0036_08 s0 | luna right | right change + true details |
| superseded_fact | Q_GEN_0022_08 s0 | luna right | right replacement + true details |
| temporal_order | Q_GEN_0004_28 s3 | luna right | right order + dates |
| temporal_order | Q_GEN_0023_02 s2 | luna right | right order + readings |
| single_hop_fact | Q_GEN_0042_14 s1 | luna right (lenient) | right issue; omits the quarantine |
| single_hop_fact | Q_GEN_0068_14 s0 | luna right | right anomaly + node counts |

Result: 10/12 luna right, 2/12 borderline, 0/12 a clear wrong answer that luna let through.
sol acts as a precision judge: 821/936 of its WRONG-where-luna-CORRECT reasons cite added details
(keyword match), and those predictions are longer (median 69 vs 42 words where the judges agree).
Caveat: 12 cases is a small sample; the constraint_recall pattern (validity periods reported from memory)
is systematic (kappa 0.04 there) and is consistent with the method's validity design, not an answer error.
