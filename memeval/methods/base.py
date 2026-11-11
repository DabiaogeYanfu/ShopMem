"""The interface every memory method implements.

The runner creates one method instance per history, calls `ingest` once with the full
interaction stream, then `retrieve` once per question. Any LLM use during ingestion
must go through `self.llm.complete(..., stage='write')` so write-time cost is metered.
`retrieve` returns the context handed to the shared answer prompt plus provenance: the
turn ids the context was built from, used for evidence recall and gate-loss analysis.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ..llm import count_tokens


@dataclass
class Retrieved:
    context: str
    provenance: list                           # opaque turn ids the context draws on
    meta: dict = field(default_factory=dict)   # method-specific diagnostics (k*, depths, ...)


class MemoryMethod(ABC):
    name = 'base'
    uses_oracle = False  # True only for baselines that read Gold labels

    def __init__(self, llm, lang, gold=None, **config):
        self.llm, self.lang, self.config = llm, lang, config
        self.gold = gold if self.uses_oracle else None

    @abstractmethod
    def ingest(self, history):
        """Build memory from the whole History (turns in chronological session order)."""

    @abstractmethod
    def retrieve(self, query):
        """Return a Retrieved context for a Query (question text, as_of, ids only)."""

    def memory_stats(self):
        """Size of the stored memory, e.g. {'entries': n, 'tokens': m}."""
        return {}


def render_turn(t, lang):
    kind = t.turn_type if not t.tool_name else f'{t.turn_type}:{t.tool_name}'
    return f'[{t.timestamp}] {t.speaker_id} ({t.speaker_role}, {kind}): {t.text}'


def render_session_header(s):
    return f'=== {s.session_id} | {s.site_id} | {s.session_start} ==='


def fit_recent(blocks, budget):
    """Keep the most recent (header, lines, ids) session blocks within a token budget.

    Truncation drops whole turns from the oldest end; a session whose header survives
    keeps its header. Returns (text, provenance ids, truncated flag).
    """
    out, ids, used, truncated = [], [], 0, False
    for header, lines in reversed(blocks):
        kept = []
        for line, tid in reversed(lines):
            n = count_tokens(line) + 1
            if used + n > budget:
                truncated = True
                break
            kept.append((line, tid))
            used += n
        if kept:
            used += count_tokens(header) + 1
            out.append([header] + [ln for ln, _ in reversed(kept)])
            ids.extend(tid for _, tid in kept)
        if truncated:
            break
    text = '\n'.join(line for block in reversed(out) for line in block)
    return text, sorted(ids), truncated
