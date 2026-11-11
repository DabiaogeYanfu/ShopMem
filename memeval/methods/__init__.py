"""Registry of memory methods runnable by `python -m memeval.run --method <name>`."""
from .context_baselines import FullContext, ObservationMask, OracleGate, SlidingWindow
from .rag import RAG, RAGBM25, OracleGateRAG, RAGDense
from .shopmem import ShopMem
from .shopmem_reader import ShopMemReader
from .shopmem_writer import ShopMemWriter
from .simplemem_adapter import SimpleMem

METHODS = {m.name: m for m in [FullContext, SlidingWindow, ObservationMask, OracleGate,
                               RAGDense, RAGBM25, RAG, OracleGateRAG, SimpleMem, ShopMem, ShopMemWriter, ShopMemReader]}
