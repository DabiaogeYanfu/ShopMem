"""LLM access: an OpenAI-compatible client (Qwen/DashScope, DeepSeek, vLLM, ...) with a
disk cache and per-stage usage metering, plus a dry-run stub for offline pipeline tests.

Every call is tagged with a stage ('write' during ingestion, 'answer', 'judge') so that
write-time cost -- the thing memory systems trade against query-time context -- is
reported separately.
"""
import hashlib
import json
import os
import sqlite3
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import tiktoken

_ENC = tiktoken.get_encoding('o200k_base')


def count_tokens(text):
    return len(_ENC.encode(text))


@dataclass
class Completion:
    text: str
    prompt_tokens: int
    completion_tokens: int
    latency: float
    cached: bool


class UsageMeter:
    """Thread-safe per-stage totals: calls, prompt/completion tokens, wall time of uncached calls."""

    def __init__(self):
        self._lock = threading.Lock()
        self.totals = defaultdict(lambda: defaultdict(float))

    def add(self, stage, c):
        with self._lock:
            t = self.totals[stage]
            t['calls'] += 1
            t['cached_calls'] += c.cached
            t['prompt_tokens'] += c.prompt_tokens
            t['completion_tokens'] += c.completion_tokens
            t['latency_s'] += c.latency

    def snapshot(self):
        with self._lock:
            return {s: dict(v) for s, v in self.totals.items()}


class DiskCache:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=60)
        self._db.execute('CREATE TABLE IF NOT EXISTS c (k TEXT PRIMARY KEY, v TEXT)')
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            row = self._db.execute('SELECT v FROM c WHERE k=?', (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key, value):
        with self._lock:
            self._db.execute('INSERT OR REPLACE INTO c VALUES (?, ?)', (key, json.dumps(value, ensure_ascii=False)))
            self._db.commit()


class LLM:
    """Base: metering and caching around a backend-specific `_call`."""

    def __init__(self, model, meter=None, cache=None, temperature=0.0, max_tokens=1024):
        self.model, self.temperature, self.max_tokens = model, temperature, max_tokens
        self.meter = meter or UsageMeter()
        self.cache = cache
        self.default_params = {}
        self.cache_tag = None  # set by backends whose replies must not share cache entries with chat calls

    def complete(self, messages, stage, **params):
        params = {'temperature': self.temperature, 'max_tokens': self.max_tokens, **self.default_params, **params}
        ident = [self.model, messages, params] + ([self.cache_tag] if self.cache_tag else [])
        key = hashlib.sha256(json.dumps(ident, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        hit = self.cache.get(key) if self.cache else None
        if hit:
            c = Completion(hit['text'], hit['prompt_tokens'], hit['completion_tokens'], 0.0, True)
        else:
            t0 = time.perf_counter()
            text, pt, ct = self._call(messages, params)
            c = Completion(text, pt, ct, time.perf_counter() - t0, False)
            if self.cache:
                self.cache.put(key, {'text': text, 'prompt_tokens': pt, 'completion_tokens': ct})
        self.meter.add(stage, c)
        return c

    def _call(self, messages, params):
        raise NotImplementedError


class OpenAICompatLLM(LLM):
    """api=responses sends chat messages through the Responses API: system messages become
    `instructions` (on relays that prepend their own system prompt to chat requests, this replaces it),
    max_tokens becomes max_output_tokens, and seed is left out of the request (it stays in the cache key).
    The reply is the first output message only: sol may emit a `commentary` message (e.g. a search
    action) and then a `final_answer` one in the same response, which `output_text` would glue
    together; chat completions on the main endpoint return just the first. The cache key of a
    Responses call carries the API tag, so it never replays as (or from) a chat call."""

    def __init__(self, model, base_url, api_key_env, retries=5, api="chat", **kw):
        super().__init__(model, **kw)
        self.api = api
        if api != 'chat':
            self.cache_tag = api
        from openai import OpenAI
        key = os.environ.get(api_key_env) if api_key_env else 'EMPTY'  # local vLLM needs no key
        if not key:
            raise RuntimeError(f'environment variable {api_key_env} is not set')
        self.client = OpenAI(base_url=base_url, api_key=key)
        self.retries = retries

    def _call(self, messages, params):
        for attempt in range(self.retries):
            try:
                if self.api == 'responses':
                    return self._responses(messages, params)
                r = self.client.chat.completions.create(model=self.model, messages=messages, **params)
                u = r.usage
                return r.choices[0].message.content or '', u.prompt_tokens, u.completion_tokens
            except Exception:
                if attempt == self.retries - 1:
                    raise
                time.sleep(2 ** attempt)

    def _responses(self, messages, params):
        params = dict(params)
        params.pop('seed', None)
        if 'max_tokens' in params:
            params['max_output_tokens'] = params.pop('max_tokens')
        system = '\n\n'.join(m['content'] for m in messages if m['role'] == 'system')
        turns = [{'role': m['role'], 'content': m['content']} for m in messages if m['role'] != 'system']
        r = self.client.responses.create(model=self.model, instructions=system or None, input=turns, **params)
        msgs = [o for o in r.output if o.type == 'message']
        text = ''.join(c.text for c in msgs[0].content if getattr(c, 'text', None)) if msgs else ''
        u = r.usage
        return text, u.input_tokens, u.output_tokens


class DryRunLLM(LLM):
    """Offline stub: no network. Answers with a fixed string, judges everything WRONG."""

    def __init__(self, **kw):
        super().__init__('dry-run', **kw)

    def _call(self, messages, params):
        prompt = '\n'.join(m['content'] for m in messages)
        text = '{"label": "WRONG", "reason": "dry run"}' if 'CORRECT' in prompt else 'DRY-RUN ANSWER'
        return text, count_tokens(prompt), count_tokens(text)


def make_llm(model, base_url=None, api_key_env=None, dry_run=False, cache_path=None, meter=None, extra_body=None,
             api='chat', **kw):
    """`extra_body` is sent with every request, e.g. {"chat_template_kwargs": {"enable_thinking": false}}
    to turn off Qwen3 thinking on vLLM; it is part of the cache key."""
    cache = DiskCache(cache_path) if cache_path and not dry_run else None
    if dry_run:
        return DryRunLLM(meter=meter, **kw)
    llm = OpenAICompatLLM(model, base_url, api_key_env, meter=meter, cache=cache, api=api, **kw)
    llm.default_params = {'extra_body': extra_body} if extra_body else {}
    return llm
