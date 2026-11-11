"""Append the request units (ShopMem-Writer request_units) to finished bank files, e.g. the train banks
of the SFT Writer that memeval.teacher wrote (runs/topr1_train_K20/banks), for the Reader GRPO pool and
rollouts. Same rule and code path as a Writer run with request_units=true: the windows of each history,
every person request turn no unit cites -> one unit (source "request").

    python scripts/add_request_units.py --pool train --K 20 --banks runs/topr1_train_K20/banks \
        --out runs/train_K20_banks_req
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from memeval.data import load, read_jsonl  # noqa: E402
from memeval.memory_ops import MemoryBank, Unit  # noqa: E402
from memeval.methods.shopmem_writer import ShopMemWriter  # noqa: E402
from memeval.windows import session_windows  # noqa: E402


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pool", default="train")
    p.add_argument("--K", type=int, default=20)
    p.add_argument("--banks", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    histories, _, _ = load("en", a.pool, a.K)
    for h in histories:
        units = [Unit.from_dict(d) for d in read_jsonl(Path(a.banks) / f"{h.history_id}.jsonl")]
        w = ShopMemWriter(None, "en", embed_base_url="", request_units=True)
        w._wcfg()
        w.history, w.turn_by_id = h, {t.dialog_id: t for t in h.turns()}
        from memeval.methods.shopmem import ShiftCalendar
        from memeval.data import DATA_DIRS, ROOT
        w.calendar = ShiftCalendar(json.loads((ROOT / "data" / DATA_DIRS["en"] / "production_calendar.json").read_text()))
        w.stats = {}
        w.bank = MemoryBank()
        for u in units:
            w.bank.units.append(u)
            w.bank.by_id[u.uid] = u
        w.add_request_units(session_windows(h, w.window_size, w.overlap, w.history_turns))
        (out / f"{h.history_id}.jsonl").write_text("".join(json.dumps(u.to_dict(), ensure_ascii=False) + "\n"
                                                           for u in w.bank.units))
        print(h.history_id, len(units), "->", len(w.bank.units), "request units", w.stats["request_units"])


if __name__ == "__main__":
    main()
