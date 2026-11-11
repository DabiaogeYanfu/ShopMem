"""Evaluate predicted working-memory snapshots against gold JSONL.

Prediction format: one JSON object per line with working_memory_id and lists
active_memory_ids, pending_item_ids, constraints.
"""
import argparse, json
from pathlib import Path

def f1(gold, pred):
    g, p = set(gold), set(pred); hit=len(g&p)
    return 2*hit/(len(g)+len(p)) if g or p else 1.0

def main(gold_path, pred_path):
    gold={json.loads(x)['working_memory_id']:json.loads(x) for x in Path(gold_path).read_text().splitlines() if x.strip()}
    pred={json.loads(x)['working_memory_id']:json.loads(x) for x in Path(pred_path).read_text().splitlines() if x.strip()}
    rows=[]
    for key,g in gold.items():
        p=pred.get(key,{})
        facts=[x['memory_id'] for x in g['active_facts']]
        pf=p.get('active_memory_ids',[])
        todos=[x['item_id'] for x in g['pending_items']]
        rows.append({'working_memory_id':key,'fact_f1':f1(facts,pf),'pending_f1':f1(todos,p.get('pending_item_ids',[])),'constraint_f1':f1(g['constraints'],p.get('constraints',[]))})
    avg={k:round(sum(r[k] for r in rows)/len(rows),4) for k in ('fact_f1','pending_f1','constraint_f1')} if rows else {}
    print(json.dumps({'snapshots':len(rows),'macro':avg,'rows':rows},ensure_ascii=False,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser(); p.add_argument('gold'); p.add_argument('pred'); a=p.parse_args(); main(a.gold,a.pred)
