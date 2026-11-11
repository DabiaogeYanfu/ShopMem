"""ShopMem-Reader as a memory method (方法论_v3_大纲 §3.4), for evaluation through memeval.run.

The memory is not rebuilt: ingest loads the bank a ShopMem-Writer run saved with bank_path
(<bank_dir>/<history_id>.jsonl), so every Reader is compared on the same memory. The answer LLM of
the run (--model / --base-url, e.g. the Reader adapter served by vLLM) is the Reader; the retrieval
planner stays the evaluation's 8B (planner_* keys), as in every other ShopMem run. Every Reader
request is sent with Qwen3 thinking off (reader_extra_body), whatever --extra-body says: the Reader
is trained on no-thinking prompts (scripts/train_reader_sft.py), so serving it with thinking on would
render a different prompt. memeval.run calls
answer() instead of its one-shot answer prompt (memeval.reader.run_episode); retrieve() is the
first retrieval only, for the interface.

Config (--method-config): bank_dir (required), t_max (2; 0 = answer from the first retrieval, same
context as the one-shot pipeline), planner_model / planner_base_url / planner_api_key_env /
planner_extra_body (Qwen3-8B on localhost:8001, thinking off), reader_temperature (0.0),
reader_extra_body (JSON, default thinking off; merged over the run's --extra-body), every
ShopMem-Writer read key (closed_view, embed_*, rerank_*, ...).
"""
import json

from ..llm import OpenAICompatLLM
from ..reader import bank_file, banked_writer, run_episode, search
from .base import MemoryMethod

NOTHINK = {'chat_template_kwargs': {'enable_thinking': False}}


class ShopMemReader(MemoryMethod):
    name = 'shopmem_reader'
    thread_safe = True

    def _planner(self):
        c = self.config
        extra = c.get('planner_extra_body', NOTHINK)
        extra = json.loads(extra) if isinstance(extra, str) else extra
        p = OpenAICompatLLM(c.get('planner_model', 'Qwen3-8B'), c.get('planner_base_url', 'http://localhost:8001/v1'),
                            c.get('planner_api_key_env', ''), meter=self.llm.meter,
                            cache=getattr(self.llm, 'cache', None))
        p.default_params = {'extra_body': extra} if extra else {}
        return p

    def ingest(self, history):
        if not self.config.get('bank_dir'):
            raise ValueError('shopmem_reader needs bank_dir (a ShopMem-Writer run saved with bank_path)')
        keys = ('bank_dir', 't_max', 'reader_temperature', 'reader_extra_body') + \
            tuple(k for k in self.config if k.startswith('planner_'))
        cfg = {k: v for k, v in self.config.items() if k not in keys}
        self.t_max = int(self.config.get('t_max', 2))
        self.temperature = float(self.config.get('reader_temperature', 0.0))
        self.memory = banked_writer(self._planner(), history, bank_file(self.config['bank_dir'], history.history_id), cfg)
        self.memory.finish()  # validity views; bank_path is not passed on, so nothing is written

    def retrieve(self, query):
        return self.memory.retrieve(query)

    def answer(self, q, llm):
        """One Reader episode; the row fields memeval.run writes besides the question's own."""
        extra = self.config.get('reader_extra_body', NOTHINK)
        extra = json.loads(extra) if isinstance(extra, str) else extra
        extra = {**llm.default_params.get('extra_body', {}), **extra}
        ep = run_episode(llm, self.memory, q, self.t_max, temperature=self.temperature, extra_body=extra)
        return {'prediction': ep['prediction'], 'context_tokens': ep['context_tokens'],
                'answer_prompt_tokens': ep['prompt_tokens'], 'provenance': ep['provenance'],
                'retrieve_s': ep['retrieve_s'],
                'meta': {'searches': ep['searches'], 'format_ok': ep['format_ok'],
                         'lenient_replies': ep['lenient_replies'], 'steps': ep['steps']},
                'raw_answer': ep['messages'][-1]['content'] if ep['messages'][-1]['role'] == 'assistant' else None}

    def memory_stats(self):
        return self.memory.memory_stats()
