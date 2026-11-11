"""Move the cache entries that pre-fix micu-gpt calls wrote WITHOUT the Responses cache tag out of
.cache/llm.sqlite (2026-10-10). Before commit 01ef34b, a Responses call on micu-gpt cached its reply
under the same key as a chat call with that model / messages / params, so a later yan-gpt chat run
could replay it (incl. double-message replies glued by output_text). Entries are copied to
.cache/quarantine_micu_prefix.sqlite, then deleted from the main cache in one transaction.

Identification: every key is rebuilt from the run files (all assistant prefixes x the FormatRetry seeds,
untagged and tagged; judge prompts of every row). The main-cache rows written since the micu run started
(rowid > last yan-run entry) are then classified exhaustively; a reply shaped like a Reader action or a
judge verdict that matches no rebuilt key is reported (should be 0).

    python -m scripts.cache_quarantine_micu_prefix            # dry run: report only
    python -m scripts.cache_quarantine_micu_prefix --apply
"""
import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from pathlib import Path

from memeval.data import load, read_jsonl
from memeval.prompts import judge_messages
from memeval.reader_cold import FORMAT_HINT, HINT
from memeval.reader_format import parse_action

CACHE = Path('.cache/llm.sqlite')
QUAR = Path('.cache/quarantine_micu_prefix.sqlite')
SOL, LUNA = 'gpt-6.1-sol', 'gpt-6-luna'
GEN = {'temperature': 1.0, 'max_tokens': 1024}
JUDGE = {'temperature': 0.0, 'max_tokens': 256}


def key(model, msgs, params, tag=None):
    ident = [model, msgs, params] + ([tag] if tag else [])
    return hashlib.sha256(json.dumps(ident, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _verdict(t):
    try:
        return json.loads(t[t.index('{'): t.rindex('}') + 1]).get('label') in ('CORRECT', 'WRONG')
    except (ValueError, AttributeError, json.JSONDecodeError):
        return False


def rows_of(run, extra=()):
    rows = read_jsonl(f'runs/{run}/trajectories.jsonl')
    for f in extra:
        p = Path(f'runs/{run}/{f}')
        if p.exists():
            rows += read_jsonl(p)
    return rows


def gen_keys(rows):
    """(untagged, tagged) -> {key: (question_id, sample)} for every teacher call a row may have made."""
    out = ({}, {})
    for r in rows:
        m = r['messages']
        s0 = {'role': 'system', 'content': m[0]['content'] + (HINT if r['hinted'] else '') + FORMAT_HINT}
        for j, x in enumerate(m):
            if x['role'] != 'assistant':
                continue
            for s in (r['sample'], 1000 + r['sample'], 2000 + r['sample']):
                p = GEN | {'seed': s}
                out[0][key(SOL, [s0] + m[1:j], p)] = (r['question_id'], r['sample'])
                out[1][key(SOL, [s0] + m[1:j], p, 'responses')] = (r['question_id'], r['sample'])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    _, qs, _ = load('en', 'train', 20)
    Q = {q.question_id: q for q in qs}
    db = sqlite3.connect(str(CACHE), timeout=60)
    rid = lambda k: (db.execute('SELECT rowid FROM c WHERE k=?', (k,)).fetchone() or [None])[0]

    yan = rows_of('reader_cold_sol_K20')
    micu = rows_of('reader_cold_sol_K20_micu', ['quarantine_prefix_bug.jsonl'])
    parity = rows_of('endpoint_parity_micu')
    yan_un, _ = gen_keys(yan)
    yan_max = max(v for v in map(rid, yan_un) if v)
    micu_un, micu_tag = gen_keys(micu + parity)

    move, replay = {}, Counter()
    for k, qk in micu_un.items():
        v = rid(k)
        if v is not None and v > yan_max:
            move[v] = 'sol_gen_untagged'
    # micu rows that replayed a yan-gpt chat reply from the cache (same key, same text): genuine sol output, kept;
    # listed so their teacher_endpoint (micu-gpt) can be read as mixed
    for r in rows_of('reader_cold_sol_K20_micu'):
        m = r['messages']
        s0 = {'role': 'system', 'content': m[0]['content'] + (HINT if r['hinted'] else '') + FORMAT_HINT}
        for j, x in enumerate(m):
            if x['role'] != 'assistant':
                continue
            for s in (r['sample'], 1000 + r['sample'], 2000 + r['sample']):
                got = db.execute('SELECT rowid, v FROM c WHERE k=?', (key(SOL, [s0] + m[1:j], GEN | {'seed': s}),)).fetchone()
                if got and got[0] <= yan_max and json.loads(got[1])['text'] == x['content']:
                    replay[(r['question_id'], r['sample'])] += 1
    judge_un, judge_tag, luna_j = set(), set(), set()
    for r in yan + micu + parity:
        q = Q[r['question_id']]
        msgs = judge_messages(q.category, q.question, q.reference, r['prediction'])
        judge_un.add(key(SOL, msgs, JUDGE))
        judge_tag.add(key(SOL, msgs, JUDGE, 'responses'))
        luna_j.add(key(LUNA, msgs, JUDGE))
    for k in judge_un:
        v = rid(k)
        if v is not None:
            move[v] = 'sol_judge_untagged'

    # exhaustive pass over everything written since the micu run started
    known = {}
    for ks, label in [(micu_tag, 'sol_gen_tagged'), (judge_tag, 'sol_judge_tagged'), (luna_j, 'luna_judge')]:
        for k in ks:
            v = rid(k)
            if v is not None:
                known[v] = label
    known.update(move)
    # Reader replies of the pre-fix micu window that no saved row uses (an episode cut off when the run was
    # stopped) are untagged too: move every Reader action written before the first tagged entry.
    first_tagged = min(v for v, lab in known.items() if lab == 'sol_gen_tagged')
    for v, text in db.execute('SELECT rowid, v FROM c WHERE rowid > ? AND rowid < ?', (yan_max, first_tagged)):
        t = json.loads(text)['text']
        if v not in known and (parse_action(t) is not None or parse_action(t, strict=False) is not None):
            move[v] = known[v] = 'sol_gen_untagged_orphan'
    shape = Counter()
    for v, text in db.execute('SELECT rowid, v FROM c WHERE rowid > ?', (yan_max,)):
        if v in known:
            shape[known[v]] += 1
            continue
        t = json.loads(text)['text']
        if parse_action(t) is not None or parse_action(t, strict=False) is not None:
            shape['reader-action in flight (after first tagged entry)'] += 1
        elif _verdict(t):
            shape['UNIDENTIFIED judge-verdict'] += 1
        else:
            shape['other (retrieval planner etc.)'] += 1
    print('last yan-run cache rowid', yan_max)
    print('entries since then:', dict(shape))
    print('to move:', dict(Counter(move.values())), 'total', len(move))
    print('micu rows with replayed yan-gpt replies:', len(replay), 'replies', sum(replay.values()))
    Path('runs/reader_cold_sol_K20_micu/yan_cache_replay_keys.txt').write_text(
        ''.join(f'{q}\t{s}\t{n}\n' for (q, s), n in sorted(replay.items())))  # question, sample, replies
    if not a.apply:
        return
    # Reader actions written after the first tagged entry and in no saved row yet are episodes in flight
    # (their rows are appended when they finish); they are tagged and stay.
    assert not shape['UNIDENTIFIED judge-verdict'], 'unidentified judge entries: stop and look'
    qdb = sqlite3.connect(str(QUAR))
    qdb.execute('CREATE TABLE IF NOT EXISTS c (k TEXT PRIMARY KEY, v TEXT, src_rowid INTEGER, kind TEXT)')
    ids = sorted(move)
    rows = [db.execute('SELECT k, v FROM c WHERE rowid=?', (i,)).fetchone() + (i, move[i]) for i in ids]
    qdb.executemany('INSERT OR REPLACE INTO c VALUES (?, ?, ?, ?)', rows)
    qdb.commit()
    assert qdb.execute('SELECT count(*) FROM c').fetchone()[0] >= len(ids)
    with db:  # one transaction
        db.executemany('DELETE FROM c WHERE rowid=?', [(i,) for i in ids])
    left = sum(1 for i in ids if db.execute('SELECT 1 FROM c WHERE rowid=?', (i,)).fetchone())
    print('moved', len(rows), '->', QUAR, '| still in main cache:', left)


if __name__ == '__main__':
    main()
