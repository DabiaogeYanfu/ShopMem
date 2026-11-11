"""Text embeddings for retrieval-based methods.

`Embedder` calls an OpenAI-compatible /v1/embeddings endpoint (Qwen3-Embedding-0.6B served
by vLLM on the lab server) and caches vectors in SQLite keyed by (model, text), since the
same turns recur across K settings and methods. Queries get Qwen3-Embedding's instruction
prefix; documents are embedded as-is. `FakeEmbedder` hashes character bigrams into a fixed
vector for offline tests and dry runs.
"""
import hashlib
import sqlite3
import threading
import zlib
from pathlib import Path

import numpy as np

QUERY_TASK = 'Given a question about factory operations, retrieve the dialogue turns that help answer it'


def _normalize(m):
    n = np.linalg.norm(m, axis=1, keepdims=True)
    return m / np.where(n == 0, 1, n)


class _VectorCache:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=60)
        self._db.execute('CREATE TABLE IF NOT EXISTS v (k TEXT PRIMARY KEY, vec BLOB)')
        self._lock = threading.Lock()

    def get_many(self, keys):
        out = {}
        with self._lock:
            for i in range(0, len(keys), 500):
                part = keys[i:i + 500]
                q = f"SELECT k, vec FROM v WHERE k IN ({','.join('?' * len(part))})"
                out.update({k: np.frombuffer(b, dtype=np.float32) for k, b in self._db.execute(q, part)})
        return out

    def put_many(self, items):
        with self._lock:
            self._db.executemany('INSERT OR REPLACE INTO v VALUES (?, ?)',
                                 [(k, np.asarray(v, dtype=np.float32).tobytes()) for k, v in items])
            self._db.commit()


class Embedder:
    def __init__(self, base_url, model, cache_path=None, batch_size=64):
        from openai import OpenAI
        self.client = OpenAI(base_url=base_url, api_key='EMPTY')
        self.model, self.batch_size = model, batch_size
        self.cache = _VectorCache(cache_path) if cache_path else None

    def embed(self, texts, query=False):
        if query:
            texts = [f'Instruct: {QUERY_TASK}\nQuery:{t}' for t in texts]
        keys = [hashlib.sha256(f'{self.model}\x00{t}'.encode()).hexdigest() for t in texts]
        found = self.cache.get_many(keys) if self.cache else {}
        missing = [i for i, k in enumerate(keys) if k not in found]
        for s in range(0, len(missing), self.batch_size):
            idx = missing[s:s + self.batch_size]
            r = self.client.embeddings.create(model=self.model, input=[texts[i] for i in idx])
            vecs = [np.asarray(d.embedding, dtype=np.float32) for d in r.data]
            found.update({keys[i]: v for i, v in zip(idx, vecs)})
            if self.cache:
                self.cache.put_many([(keys[i], v) for i, v in zip(idx, vecs)])
        return _normalize(np.stack([found[k] for k in keys]))


class FakeEmbedder:
    """Offline stand-in: character-bigram hashing, so lexically similar texts are close."""

    def __init__(self, dim=256):
        self.dim = dim

    def embed(self, texts, query=False):
        m = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for j in range(len(t) - 1):
                m[i, zlib.crc32(t[j:j + 2].encode()) % self.dim] += 1
        return _normalize(m)


def make_embedder(base_url, model, cache_path=None):
    return FakeEmbedder() if base_url == 'fake' else Embedder(base_url, model, cache_path)
