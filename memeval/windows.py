"""Session-bounded write windows, shared by the v2 Writer and the window -> question map.

The cut is v1 ShopMem's (`ShopMem._windows`, frozen at tag v1-final): windows never cross a
session, each holds `window_size` turns and shares `overlap` turns with the previous window
of the same session; `history` is the `history_turns` turns of the session just before the
window. `tests/test_writer.py` checks the two cuts agree turn for turn.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class Window:
    index: int              # position in the history's window stream (chronological)
    session_id: str
    site_id: str
    turns: tuple            # Turn objects
    history: tuple          # preceding turns of the same session
    new_turns: tuple        # turn ids not already in the previous window (the overlap removed)

    @property
    def turn_ids(self):
        return [t.dialog_id for t in self.turns]


def session_windows(history, window_size=16, overlap=2, history_turns=4):
    windows = []
    step = max(1, window_size - overlap)
    for s in history.sessions:
        turns = list(s.turns)
        seen = set()
        for i in range(0, max(len(turns) - overlap, 1), step):
            chunk = turns[i:i + window_size]
            if not chunk:
                continue
            new = tuple(t.dialog_id for t in chunk if t.dialog_id not in seen)
            seen.update(t.dialog_id for t in chunk)
            windows.append(Window(len(windows), s.session_id, s.site_id, tuple(chunk),
                                  tuple(turns[max(0, i - history_turns):i]), new))
    return windows
