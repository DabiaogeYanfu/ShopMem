"""SimpleMem (Liu et al., 2026) run through its official code, unmodified.

The official repository (github.com/aiming-lab/SimpleMem, commit db80b6a) is imported
from `simplemem_path` (default ~/baselines/SimpleMem). Only `simplemem.core` -- the
paper's pipeline -- is loaded: MemoryBuilder (sliding-window extraction into lossless
restatements with keywords / timestamp / persons / entities), VectorStore (LanceDB with
a Tantivy full-text index) and HybridRetriever (LLM information-requirement planning,
targeted semantic queries, keyword and structured search, up to two reflection rounds).
The package's __init__ (router / evolver extensions) is bypassed so nothing outside the
paper's method runs.

What the adapter changes, and why:
- LLM calls go through memeval's metered client (the same backbone as every other
  method); write-time calls are tagged stage='write', query-time planning/reflection
  calls stage='retrieve', so SimpleMem's query-time LLM cost is reported, not hidden.
- Embeddings come from the shared Qwen3-Embedding-0.6B server. Like the official
  EmbeddingModel with its default model name, no query instruction is added.
- Each turn is passed with its own timestamp and "speaker_id (role, message type)", the
  same information the other methods see (the official LoCoMo script passes the session
  date for every turn).
- The official LoCoMo script turns reflection off for adversarial questions using the
  gold category; categories are hidden here, so reflection runs for every question.
- Answers use memeval's shared answer prompt; the retrieved entries are formatted with
  the official AnswerGenerator._format_contexts.
- Provenance: the official entries do not record their source turns, so each entry is
  attributed to all turns of the extraction window it came from (window-level, i.e. an
  over-estimate of evidence recall; meta['provenance'] = 'window').

Settings follow the official defaults: window 40 turns with overlap 2, semantic /
keyword / structured top-k 25 / 5 / 5, planning and reflection on (2 rounds). Override
via --method-config, e.g. window_size=20 enable_reflection=false.
"""
import shutil
import sys
import threading
import types
from pathlib import Path

from ..data import ROOT
from ..embed import make_embedder
from .base import MemoryMethod, Retrieved

DEFAULT_PATH = Path.home() / 'baselines' / 'SimpleMem'


def _import_core(path):
    """Import simplemem.core from the official checkout without running simplemem/__init__.py."""
    if 'simplemem' not in sys.modules:
        pkg = types.ModuleType('simplemem')
        pkg.__path__ = [str(Path(path) / 'simplemem')]
        sys.modules['simplemem'] = pkg
    from simplemem.core.answer_generator import AnswerGenerator
    from simplemem.core.database.vector_store import VectorStore
    from simplemem.core.hybrid_retriever import HybridRetriever
    from simplemem.core.memory_builder import MemoryBuilder
    from simplemem.core.models.memory_entry import Dialogue
    from simplemem.core.utils.llm_client import LLMClient
    # the official modules print progress for every window and query; silence them (module globals only)
    for name, mod in list(sys.modules.items()):
        if name.startswith('simplemem.core'):
            mod.print = lambda *a, **k: None
    return AnswerGenerator, VectorStore, HybridRetriever, MemoryBuilder, Dialogue, LLMClient


def _metered_client_class(LLMClient):
    class MeteredLLMClient(LLMClient):
        """Official client interface (chat_completion / extract_json) backed by memeval's LLM."""

        def __init__(self, llm, max_tokens):
            self.llm, self.max_tokens, self.stage = llm, max_tokens, 'write'
            self.model, self.base_url, self.enable_thinking, self.use_streaming = llm.model, None, False, False

        def chat_completion(self, messages, temperature=0.2, response_format=None, max_retries=3):
            params = {'temperature': temperature, 'max_tokens': self.max_tokens}
            if response_format:
                params['response_format'] = response_format
            return self.llm.complete(messages, stage=self.stage, **params).text

    return MeteredLLMClient


class _EmbeddingAdapter:
    """Official EmbeddingModel interface backed by the shared embedding server."""

    def __init__(self, embedder, name):
        self.embedder, self.model_name, self.model_type = embedder, name, 'shared_embedding_server'
        self.dimension = int(embedder.embed(['dimension probe']).shape[1])
        self.supports_query_prompt = False

    def encode(self, texts, is_query=False):
        return self.embedder.embed([texts] if isinstance(texts, str) else list(texts))

    def encode_single(self, text, is_query=False):
        return self.encode([text])[0]

    def encode_query(self, queries):
        return self.encode(queries)

    def encode_documents(self, documents):
        return self.encode(documents)


class SimpleMem(MemoryMethod):
    name = 'simplemem'
    thread_safe = True  # after ingest the client stays in the 'retrieve' stage; searches only read the store

    def ingest(self, history):
        cfg = self.config
        AnswerGenerator, VectorStore, HybridRetriever, MemoryBuilder, Dialogue, LLMClient = \
            _import_core(cfg.get('simplemem_path', DEFAULT_PATH))
        self.client = _metered_client_class(LLMClient)(self.llm, int(cfg.get('max_tokens', 4096)))
        embedder = make_embedder(cfg.get('embed_base_url', 'http://localhost:8002/v1'),
                                 cfg.get('embed_model', 'Qwen3-Embedding-0.6B'),
                                 cfg.get('embed_cache', str(ROOT / '.cache' / 'embeddings.sqlite')))
        db = Path(cfg.get('db_root', ROOT / '.cache' / 'simplemem_db')) / history.history_id
        shutil.rmtree(db, ignore_errors=True)
        self.store = VectorStore(db_path=str(db), embedding_model=_EmbeddingAdapter(embedder, 'Qwen3-Embedding-0.6B'),
                                 table_name='memory')

        turns = history.turns()
        dialogues = [Dialogue(dialogue_id=i, content=t.text, timestamp=t.timestamp,
                              speaker=f'{t.speaker_id} ({t.speaker_role}, '
                                      f'{t.turn_type}{":" + t.tool_name if t.tool_name else ""})')
                     for i, t in enumerate(turns, 1)]
        turn_id = {i: t.dialog_id for i, t in enumerate(turns, 1)}
        builder = MemoryBuilder(self.client, self.store, window_size=int(cfg.get('window_size', 40)),
                                enable_parallel_processing=True,
                                max_parallel_workers=int(cfg.get('write_workers', 16)))
        # record each entry's source window (the official entries carry no provenance)
        self.provenance, lock = {}, threading.Lock()
        parse = builder._parse_llm_response

        def parse_with_provenance(response, dialogue_ids):
            entries = parse(response, dialogue_ids)
            with lock:
                for e in entries:
                    self.provenance[e.entry_id] = [turn_id[i] for i in dialogue_ids]
            return entries

        builder._parse_llm_response = parse_with_provenance
        self.client.stage = 'write'
        builder.add_dialogues(dialogues)
        builder.process_remaining()

        flag = lambda k, d: str(cfg.get(k, d)).lower() in ('1', 'true', 'yes')
        self.retriever = HybridRetriever(self.client, self.store, enable_planning=flag('enable_planning', True),
                                         enable_reflection=flag('enable_reflection', True),
                                         max_reflection_rounds=int(cfg.get('max_reflection_rounds', 2)),
                                         enable_parallel_retrieval=True,
                                         max_retrieval_workers=int(cfg.get('retrieval_workers', 8)))
        self.format_contexts = AnswerGenerator(self.client)._format_contexts
        self.client.stage = 'retrieve'

    def retrieve(self, query):
        entries = self.retriever.retrieve(query.text)
        ids = sorted({t for e in entries for t in self.provenance.get(e.entry_id, [])})
        return Retrieved(self.format_contexts(entries), ids, {'entries': len(entries), 'provenance': 'window'})

    def memory_stats(self):
        return {'entries': self.store.backend.count()}
