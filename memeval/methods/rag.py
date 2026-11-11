"""Retrieval baselines: index the raw history, retrieve a fixed top-k per question.

- rag_dense: cosine similarity of Qwen3-Embedding vectors (queries get the model's
  instruction prefix).
- rag_bm25: Okapi BM25 (k1=1.5, b=0.75). CJK runs are segmented with jieba; ASCII words
  and IDs such as PRESS-LINE-07-007 or LOT202604-088 stay whole, so exact identifier
  matches count; common Chinese / English function words are dropped. (Character
  bigrams without stopwords let generic bigrams such as 是什/什么 outscore the entity.)
- rag_hybrid: reciprocal rank fusion of the two rankings (score = sum 1/(rrf_k + rank),
  rrf_k = 60), a standard fusion with no tuned weights.
- oracle_gate_rag: rag_hybrid over the turns the gold labels mark as informative -- the
  "perfect gate + retrieval" reference. Reads Gold.

Config (via --method-config): top_k (10), unit ('turn' | 'chunk'), chunk_turns (4,
consecutive turns within one session), embed_base_url (http://localhost:8002/v1, or
'fake' for offline tests), embed_model (Qwen3-Embedding-0.6B), embed_cache.
rerank_base_url (off) / rerank_model / rerank_pool (3): the top rerank_pool x top_k units are
re-scored by a cross-encoder (Qwen3-Reranker) and the best top_k kept -- the "+ rerank"
baseline, the same reranker ShopMem uses.

Units are embedded / indexed as "speaker_role: text" (timestamps would only add noise);
the context shows the retrieved units in chronological order under their session
headers, with full timestamps.
"""
import math
import re
from collections import Counter, defaultdict

import numpy as np

from ..data import ROOT
from ..embed import make_embedder
from ..rerank import make_reranker
from .base import MemoryMethod, Retrieved, render_session_header, render_turn

_BM25_TOKEN = re.compile(r'[一-鿿]+|[a-z0-9][a-z0-9_\-.#%/]*')
STOPWORDS = set(
    '的 了 在 是 我 有 和 就 不 都 一 上 也 很 到 说 要 去 你 会 着 看 好 这 那 吗 呢 吧 啊 被 把 让 给 对 从 与 及 等 '
    '还 又 或 能 个 哪 其 之 为 以 而 并 但 已 将 由 于 中 后 前 时 下 过 来 什么 一下 一个 这个 那个 哪个 哪些 '
    '怎么 如何 为什么 没有 可以 之前 之后 时候 这边 那边 一步 我们 你们 他们 现在 目前 当时 '
    'the a an of to in on for and or is are was were be been being what which who whom when where how did '
    'does do with at by from this that these those it its as than then there their our your any some'.split())


def bm25_tokens(text):
    import jieba  # imported lazily: its dictionary load is slow and only BM25 needs it
    jieba.setLogLevel(60)
    toks = []
    for s in _BM25_TOKEN.findall(text.lower()):
        parts = jieba.lcut(s) if '一' <= s[0] <= '鿿' else [s.rstrip('.')]
        toks += [p for p in parts if p not in STOPWORDS]
    return toks


class BM25:
    def __init__(self, docs, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.tfs = [Counter(bm25_tokens(d)) for d in docs]
        self.lens = np.array([sum(tf.values()) for tf in self.tfs], dtype=np.float32)
        self.avg = float(self.lens.mean()) if len(docs) else 0.0
        df = Counter(t for tf in self.tfs for t in tf)
        n = len(docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}
        self.postings = defaultdict(list)
        for i, tf in enumerate(self.tfs):
            for t, c in tf.items():
                self.postings[t].append((i, c))

    def scores(self, query):
        s = np.zeros(len(self.tfs), dtype=np.float32)
        for t in set(bm25_tokens(query)):
            idf = self.idf.get(t)
            if idf is None:
                continue
            for i, c in self.postings[t]:
                s[i] += idf * c * (self.k1 + 1) / (c + self.k1 * (1 - self.b + self.b * self.lens[i] / self.avg))
        return s


def rrf(rankings, k=60):
    fused = defaultdict(float)
    for ranking in rankings:
        for r, i in enumerate(ranking):
            fused[i] += 1.0 / (k + r + 1)
    return sorted(fused, key=lambda i: -fused[i])


class RAG(MemoryMethod):
    name = 'rag_hybrid'
    retriever = 'hybrid'
    thread_safe = True  # retrieve() only reads the index

    def ingest(self, history):
        cfg = self.config
        self.top_k = int(cfg.get('top_k', 10))
        unit, size = cfg.get('unit', 'turn'), int(cfg.get('chunk_turns', 4))
        self.units = []  # (session, [turns])
        for s in history.sessions:
            turns = [t for t in s.turns if self.keep(t)]
            step = 1 if unit == 'turn' else size
            for i in range(0, len(turns), step):
                self.units.append((s, turns[i:i + step]))
        docs = ['\n'.join(f'{t.speaker_role}: {t.text}' for t in ts) for _, ts in self.units]
        self.bm25 = BM25(docs) if self.retriever in ('bm25', 'hybrid') else None
        self.embedder = None
        if self.retriever in ('dense', 'hybrid'):
            self.embedder = make_embedder(cfg.get('embed_base_url', 'http://localhost:8002/v1'),
                                          cfg.get('embed_model', 'Qwen3-Embedding-0.6B'),
                                          cfg.get('embed_cache', str(ROOT / '.cache' / 'embeddings.sqlite')))
            self.vectors = self.embedder.embed(docs) if docs else np.zeros((0, 1), dtype=np.float32)
        self.docs = docs
        self.reranker = make_reranker(cfg.get('rerank_base_url'), cfg.get('rerank_model', 'Qwen3-Reranker-4B'),
                                      cfg.get('rerank_cache', str(ROOT / '.cache' / 'rerank.sqlite')))

    def keep(self, turn):
        return True

    def rank(self, text):
        rankings = []
        if self.embedder is not None:
            sims = self.vectors @ self.embedder.embed([text], query=True)[0]
            rankings.append(list(np.argsort(-sims)))
        if self.bm25 is not None:
            s = self.bm25.scores(text)
            rankings.append([i for i in np.argsort(-s) if s[i] > 0])
        return rankings[0] if len(rankings) == 1 else rrf(rankings, int(self.config.get('rrf_k', 60)))

    def retrieve(self, query):
        ranked = [int(i) for i in self.rank(query.text)]
        if self.reranker:
            pool = ranked[:int(self.top_k * float(self.config.get('rerank_pool', 3)))]
            scores = self.reranker.score(query.text, [self.docs[i] for i in pool])
            ranked = [pool[j] for j in sorted(range(len(pool)), key=lambda j: -scores[j])]
        hits = sorted(ranked[:self.top_k])  # chronological order
        lines, ids, last_session = [], [], None
        for i in hits:
            s, turns = self.units[i]
            if s.session_id != last_session:
                lines.append(render_session_header(s))
                last_session = s.session_id
            lines += [render_turn(t, self.lang) for t in turns]
            ids += [t.dialog_id for t in turns]
        return Retrieved('\n'.join(lines), ids, {'hits': len(hits)})

    def memory_stats(self):
        return {'entries': len(self.units)}


class RAGDense(RAG):
    name = 'rag_dense'
    retriever = 'dense'


class RAGBM25(RAG):
    name = 'rag_bm25'
    retriever = 'bm25'


class OracleGateRAG(RAG):
    name = 'oracle_gate_rag'
    uses_oracle = True

    def ingest(self, history):
        self.low_density = self.gold.low_density[history.history_id]
        super().ingest(history)

    def keep(self, turn):
        return not self.low_density[turn.dialog_id]
