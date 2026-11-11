"""Cross-encoder relevance scores for retrieval candidates.

`Reranker` calls vLLM's /score endpoint serving Qwen3-Reranker (a causal LM scored as
P("yes"), served with --runner pooling and the Qwen3ForSequenceClassification override),
using the model's own prompt template, and caches scores in SQLite keyed by (model, query,
document). Scores are probabilities in [0, 1], so the cliff cut sees one comparable scale
for every view. `FakeReranker` scores character-bigram overlap for offline tests.
"""
import hashlib
import json
import sqlite3
import threading
import urllib.request
from pathlib import Path

PREFIX = ('<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the '
          'Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n<|im_start|>user\n')
SUFFIX = '<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'
TASK = 'Given a question about factory operations, judge whether the memory entry helps answer it'


class _ScoreCache:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=60)
        self._db.execute('CREATE TABLE IF NOT EXISTS s (k TEXT PRIMARY KEY, v REAL)')
        self._lock = threading.Lock()

    def get_many(self, keys):
        with self._lock:
            out = {}
            for i in range(0, len(keys), 500):
                part = keys[i:i + 500]
                out.update(self._db.execute(f"SELECT k, v FROM s WHERE k IN ({','.join('?' * len(part))})", part))
            return out

    def put_many(self, items):
        with self._lock:
            self._db.executemany('INSERT OR REPLACE INTO s VALUES (?, ?)', items)
            self._db.commit()


class Reranker:
    def __init__(self, base_url, model, cache_path=None, batch_size=32):
        self.url = base_url.rstrip('/').removesuffix('/v1') + '/score'
        self.model, self.batch_size = model, batch_size
        self.cache = _ScoreCache(cache_path) if cache_path else None

    def score(self, query, docs):
        q = f'{PREFIX}<Instruct>: {TASK}\n<Query>: {query}\n'
        keys = [hashlib.sha256(f'{self.model}\x00{query}\x00{d}'.encode()).hexdigest() for d in docs]
        found = self.cache.get_many(keys) if self.cache else {}
        missing = [i for i, k in enumerate(keys) if k not in found]
        for s in range(0, len(missing), self.batch_size):
            idx = missing[s:s + self.batch_size]
            body = {'model': self.model, 'text_1': q, 'text_2': [f'<Document>: {docs[i]}{SUFFIX}' for i in idx]}
            req = urllib.request.Request(self.url, json.dumps(body).encode(), {'Content-Type': 'application/json'})
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read())['data']
            scores = {d['index']: float(d['score']) for d in data}
            new = [(keys[i], scores[j]) for j, i in enumerate(idx)]
            found.update(new)
            if self.cache:
                self.cache.put_many(new)
        return [found[k] for k in keys]


class FakeReranker:
    """Offline stand-in: share of the query's character bigrams found in the document."""

    def score(self, query, docs):
        grams = {query[i:i + 2] for i in range(len(query) - 1)}
        return [sum(g in d for g in grams) / max(len(grams), 1) for d in docs]


def make_reranker(base_url, model, cache_path=None):
    if not base_url:
        return None
    return FakeReranker() if base_url == 'fake' else Reranker(base_url, model, cache_path)
