"""Validate JSONL files against lightweight required-field contracts."""
import json
from pathlib import Path

REQUIRED = {
    "scenarios.jsonl": ["scenario_id", "domain", "time_span", "synthetic"],
    "sessions.jsonl": ["scenario_id", "session_id", "turns"],
    "events.jsonl": ["event_id", "scenario_id", "event_type", "source_dialog_ids"],
    "memory_units.jsonl": ["memory_id", "scenario_id", "statement", "source_dialog_ids", "status"],
    "questions.jsonl": ["question_id", "scenario_id", "question", "answer", "evidence"],
    "working_memory.jsonl": ["working_memory_id", "scenario_id", "snapshot_time", "task_id", "active_facts", "pending_items", "constraints", "source_dialog_ids"],
}

def validate(data_dir="data/dev"):
    root = Path(data_dir); errors = []; counts = {}
    for name, fields in REQUIRED.items():
        path = root / name; counts[name] = 0
        if not path.exists(): errors.append(f"缺少文件: {path}"); continue
        for line_no, line in enumerate(path.read_text().splitlines(), 1):
            if not line.strip(): continue
            counts[name] += 1
            try: obj = json.loads(line)
            except json.JSONDecodeError as e: errors.append(f"{name}:{line_no} JSON错误 {e}"); continue
            for field in fields:
                if field not in obj: errors.append(f"{name}:{line_no} 缺少字段 {field}")
    # Cross-file provenance checks catch silent benchmark corruption.
    def load(name, key):
        p=root/name
        return {json.loads(x)[key] for x in p.read_text().splitlines() if x.strip()} if p.exists() else set()
    dialog_ids={d["dialog_id"] for row in (json.loads(x) for x in (root/"sessions.jsonl").read_text().splitlines() if x.strip()) for d in row["turns"]} if (root/"sessions.jsonl").exists() else set()
    event_ids=load("events.jsonl", "event_id"); memory_ids=load("memory_units.jsonl", "memory_id")
    for name in ("events.jsonl", "memory_units.jsonl", "questions.jsonl", "working_memory.jsonl"):
        p=root/name
        if not p.exists(): continue
        for n,line in enumerate(p.read_text().splitlines(),1):
            if not line.strip(): continue
            o=json.loads(line)
            for did in o.get("source_dialog_ids", []) + o.get("evidence", []):
                if did not in dialog_ids: errors.append(f"{name}:{n} 引用了不存在的 dialog_id {did}")
            if name == "questions.jsonl":
                for eid in o.get("required_events", []):
                    if eid not in event_ids: errors.append(f"{name}:{n} 引用了不存在的 event_id {eid}")
            if name == "working_memory.jsonl":
                for fact in o.get("active_facts", []):
                    if fact.get("memory_id") not in memory_ids: errors.append(f"{name}:{n} 引用了不存在的 memory_id {fact.get('memory_id')}")
                if not o.get("task_id"): errors.append(f"{name}:{n} task_id 不能为空")
    # A task snapshot must have a source timestamp and at least one source turn.
    wm_path=root/"working_memory.jsonl"
    if wm_path.exists():
        for n,line in enumerate(wm_path.read_text().splitlines(),1):
            if not line.strip(): continue
            o=json.loads(line)
            if not o.get("source_dialog_ids"): errors.append(f"working_memory.jsonl:{n} 必须有 provenance")
    return counts, errors

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(); p.add_argument("--data-dir", default="data/dev"); args = p.parse_args()
    counts, errors = validate(args.data_dir)
    print(json.dumps({"counts": counts, "errors": errors}, ensure_ascii=False, indent=2))
    raise SystemExit(1 if errors else 0)
