"""Baselines that put raw history into the context window, with no retrieval.

- full_context: the whole history, truncated from the oldest end to `max_context_tokens`
  (the backbone's window minus room for the prompt and answer).
- sliding_window: the most recent `window_tokens` of history. Set the budget to the
  proposed method's mean context size to compare at equal token cost.
- observation_mask: full context with old tool observations replaced by a placeholder
  and agent think turns dropped; the most recent `keep_last` observations stay intact
  (Lindenbauer et al., 2025, "The Complexity Trap").
- oracle_gate: full context restricted to turns the gold labels mark as informative --
  a perfect semantic gate without retrieval. Reads Gold; reported as a reference only.
"""
from .base import MemoryMethod, Retrieved, fit_recent, render_session_header, render_turn

# v0.5 data: agent_tool_call is the Agent's JSON request, tool_observation the system's JSON reply
OBSERVATION_TYPES = {'tool_observation', 'tool_call'}
MASK = {'zh': '[{tool} 的返回已省略]', 'en': '[{tool} output omitted]'}


class FullContext(MemoryMethod):
    name = 'full_context'
    default_budget_key, default_budget = 'max_context_tokens', 120_000

    def ingest(self, history):
        self.blocks = [(render_session_header(s), [(render_turn(t, self.lang), t.dialog_id)
                                                   for t in s.turns if self.keep(t)])
                       for s in history.sessions]
        self.blocks = [self.transform_block(b) for b in self.blocks]
        budget = int(self.config.get(self.default_budget_key, self.default_budget))
        self.text, self.ids, self.truncated = fit_recent(self.blocks, budget)

    def keep(self, turn):
        return True

    def transform_block(self, block):
        return block

    def retrieve(self, query):
        return Retrieved(self.text, self.ids, {'truncated': self.truncated})

    def memory_stats(self):
        return {'entries': len(self.ids)}


class SlidingWindow(FullContext):
    name = 'sliding_window'
    default_budget_key, default_budget = 'window_tokens', 4_000


class ObservationMask(FullContext):
    name = 'observation_mask'

    def ingest(self, history):
        turns = [t for s in history.sessions for t in s.turns]
        obs = [t.dialog_id for t in turns if t.turn_type in OBSERVATION_TYPES]
        keep_last = int(self.config.get('keep_last', 10))
        self.visible = set(obs[-keep_last:]) if keep_last else set()
        self.by_id = {t.dialog_id: t for t in turns}
        super().ingest(history)

    def keep(self, turn):
        return turn.turn_type != 'agent_think'

    def transform_block(self, block):
        header, lines = block
        out = []
        for line, tid in lines:
            t = self.by_id[tid]
            if t.turn_type in OBSERVATION_TYPES and tid not in self.visible:
                masked = MASK[self.lang].format(tool=t.tool_name or t.speaker_id)
                line = line[: line.index('): ') + 3] + masked
            out.append((line, tid))
        return header, out


class OracleGate(FullContext):
    name = 'oracle_gate'
    uses_oracle = True

    def ingest(self, history):
        self.low_density = self.gold.low_density[history.history_id]
        super().ingest(history)

    def keep(self, turn):
        return not self.low_density[turn.dialog_id]
