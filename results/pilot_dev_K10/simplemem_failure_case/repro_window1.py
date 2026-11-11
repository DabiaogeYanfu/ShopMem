# Reproduces the SimpleMem window-1 extraction truncation on runs/pilot_simplemem_K10's
# history (H_ZH_DEV_K010_001). Run on the lab server (`~/envs/memeval/bin/python`) with
# M-locomo synced to ~/M-locomo and the Qwen3-8B server up on :8001. Writes
# /tmp/window1_evidence.json (copied here as window1_truncated_response.json).
import json
import sys
sys.path.insert(0, '.')
from memeval.methods.simplemem_adapter import _import_core, _metered_client_class, DEFAULT_PATH
from memeval.llm import make_llm
from memeval.data import load, ROOT

AnswerGenerator, VectorStore, HybridRetriever, MemoryBuilder, Dialogue, LLMClient = _import_core(DEFAULT_PATH)

llm = make_llm('Qwen3-8B', 'http://localhost:8001/v1', '', dry_run=False, cache_path=str(ROOT / '.cache' / 'llm.sqlite'),
               max_tokens=4096, extra_body={"chat_template_kwargs": {"enable_thinking": False}})
client = _metered_client_class(LLMClient)(llm, 4096)
client.stage = 'write'

histories, questions, gold = load('zh', 'dev', 10)
h = [x for x in histories if x.history_id == 'H_ZH_DEV_K010_001'][0]
turns = h.turns()
window_turns = turns[0:40]
dialogues = [Dialogue(dialogue_id=i, content=t.text, timestamp=t.timestamp,
                      speaker=f'{t.speaker_id} ({t.speaker_role}, {t.turn_type}{":" + t.tool_name if t.tool_name else ""})')
             for i, t in enumerate(window_turns, 1)]

builder = MemoryBuilder(client, None, window_size=40, enable_parallel_processing=False, max_parallel_workers=1)
dialogue_text = "\n".join([str(d) for d in dialogues])
dialogue_ids = [d.dialogue_id for d in dialogues]
prompt = builder._build_extraction_prompt(dialogue_text, dialogue_ids, "")

messages = [
    {"role": "system", "content": "You are a professional information extraction assistant, skilled at extracting structured, unambiguous information from conversations. You must output valid JSON format."},
    {"role": "user", "content": prompt},
]
c = llm.complete(messages, stage='write', temperature=0.1, max_tokens=4096)
resp = c.text

out = {
    'window_turns': [t.dialog_id for t in window_turns],
    'window_dialogue_text': dialogue_text,
    'prompt_chars': len(prompt),
    'response_chars': len(resp),
    'response_prompt_tokens': c.prompt_tokens,
    'response_completion_tokens': getattr(c, 'completion_tokens', None),
    'raw_response': resp,
}
try:
    entries = builder._parse_llm_response(resp, dialogue_ids)
    out['parse_result'] = f'OK: {len(entries)} entries'
except Exception as e:
    out['parse_result'] = f'FAILED: {e!r}'

with open('/tmp/window1_evidence.json', 'w') as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print('parse_result:', out['parse_result'])
print('response_chars:', out['response_chars'], 'prompt_tokens:', out['response_prompt_tokens'])
