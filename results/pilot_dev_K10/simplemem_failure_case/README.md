Supporting evidence for the SimpleMem write-stage cliff-truncation failure case.
Write-up (paper-facing): `ShopMem/稿件/SimpleMem失效案例-断崖截断.md`.

- `failed_questions.json` — all 38 dev-K10 questions about X6132-M3-081/WO_0081 from
  `runs/pilot_simplemem_K10` (question, gold reference, SimpleMem prediction, judge verdict).
  36/36 answerable ones are wrong; 3 unanswerable ones are coincidentally "correct".
- `window1_truncated_response.json` — replay of the write-stage extraction call for window 1
  (turns 1-40, the window containing the only alarm→diagnosis→fix episode in this history).
  `raw_response` is the actual truncated model output (cut off at 4096 output tokens, 26 valid
  entries in, 27th mid-entry); `parse_result` shows the resulting parse failure.
- `repro_window1.py` — script that reproduces the above; run with
  `~/envs/memeval/bin/python` on the lab server (Qwen3-8B serving on :8001).
