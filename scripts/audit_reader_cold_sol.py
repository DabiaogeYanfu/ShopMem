"""Independent audit of the sol Reader cold-start data (2026-10-10). Re-derives every property from the
raw files instead of trusting the flags written at generation time.
    ~/envs/memeval/bin/python -m scripts.audit_reader_cold_sol      -> results/reader_cold_sol_audit.json"""
import collections, hashlib, json, re, sqlite3, statistics as st
from pathlib import Path
from memeval.data import load, read_jsonl
from memeval.prompts import judge_messages
from memeval.reader import reader_system
from memeval.reader_cold import FORMAT_HINT, HINT
from memeval.reader_format import parse_action
from memeval.score import parse_label

RUNS = {'yan': 'reader_cold_sol_K20', 'micu': 'reader_cold_sol_K20_micu', 'yanredo': 'reader_cold_sol_K20_yanredo'}
SFT = Path('runs/reader_cold_sol_K20_all_v2/sft')
OVERRIDES = Path('results/reader_cold_sol_rejudge_conflicts.json')
issues, report = collections.defaultdict(list), {}


def issue(kind, detail):
    issues[kind].append(detail)


hs, qs, _ = load('en', 'train', 20)
Q = {q.question_id: q for q in qs}
H = {h.history_id: h for h in hs}
R = {n: read_jsonl(f'runs/{d}/trajectories.jsonl') for n, d in RUNS.items()}
rows = [r | {'_run': n} for n, rs in R.items() for r in rs]

# 1. coverage: every (question, sample) exactly once, metadata agrees with the dataset
keys = collections.Counter((r['question_id'], r['sample']) for r in rows)
want = {(q.question_id, k) for q in qs for k in range(4)}
report['coverage'] = {'rows': len(rows), 'unique': len(keys), 'expected': len(want),
                      'missing': len(want - set(keys)), 'extra': len(set(keys) - want),
                      'duplicated': sum(c > 1 for c in keys.values())}
for r in rows:
    q = Q.get(r['question_id'])
    if q is None:
        issue('unknown_question', r['question_id']); continue
    if r['history_id'] != q.history_id or r['category'] != q.category:
        issue('metadata_mismatch', (r['question_id'], r['sample']))
    if r['hinted'] != (r['sample'] < 2):
        issue('hinted_flag_wrong', (r['question_id'], r['sample']))
    exp_ep = {'yan': 'yan-gpt', 'micu': 'micu-gpt', 'yanredo': 'yan-gpt'}[r['_run']]
    if r.get('teacher_endpoint') != exp_ep:
        issue('endpoint_tag_wrong', (r['_run'], r['question_id'], r['sample'], r.get('teacher_endpoint')))

# 2. conversation structure, re-derived
for r in rows:
    m, k = r['messages'], (r['_run'], r['question_id'], r['sample'])
    q = Q[r['question_id']]
    roles = [x['role'] for x in m]
    if roles[:2] != ['system', 'user'] or roles[-1] != 'assistant' or any(
            roles[i] == roles[i + 1] for i in range(1, len(roles) - 1)):
        issue('bad_role_sequence', k)
    sys_prompt = m[0]['content']
    if HINT.strip() in sys_prompt or FORMAT_HINT.strip() in sys_prompt:
        issue('hint_saved', k)
    if not m[1]['content'].endswith(f'Question: {q.question}'):
        issue('question_text_mismatch', k)
    if f'as of {q.as_of}'.lower() not in sys_prompt.lower() and q.as_of not in sys_prompt:
        issue('as_of_missing_in_system', k)
    acts = [parse_action(x['content']) for x in m if x['role'] == 'assistant']
    strict = all(a is not None for a in acts)
    if strict != r['format_ok'] and not (r['format_ok'] is False and strict and acts[-1]['action'] != 'answer'):
        issue('format_flag_disagrees', k)
    if any(x['role'] == 'assistant' and x['content'].count('"action"') > 1 for x in m):
        issue('glued_reply', k)
    if not acts or acts[-1] is None or acts[-1]['action'] != 'answer':
        issue('not_ending_in_answer', k)
    elif acts[-1]['answer'] != r['prediction']:
        issue('prediction_mismatch', k)
    n_search = sum(1 for a in acts if a and a['action'] == 'search')
    if n_search != len(r['searches']) or n_search > 2:
        issue('search_count_mismatch', k)
    for a, s in zip([a for a in acts if a and a['action'] == 'search'], r['searches']):
        if a['query'] != s['query'] or a['include_closed'] != s['include_closed']:
            issue('search_record_mismatch', k)
    results = [x['content'] for x in m[2:] if x['role'] == 'user']
    for i, (s, res) in enumerate(zip(r['searches'], results)):
        if not res.startswith(f'Search results for "{s["query"]}" (include_closed={str(s["include_closed"]).lower()})'):
            issue('search_result_header_mismatch', k)
        if not res.rstrip().endswith(f'Searches left: {2 - (i + 1)}.'):
            issue('searches_left_wrong', k)
    if any('No more searches' in x for x in results):
        issue('forced_answer', k)

# 3. same question -> same first retrieval in all samples / runs (the retrieval side is deterministic)
first = collections.defaultdict(set)
for r in rows:
    first[r['question_id']].add(r['messages'][1]['content'])
report['first_retrieval_variants'] = dict(collections.Counter(len(v) for v in first.values()))
for q, v in first.items():
    if len(v) > 1:
        issue('first_retrieval_differs', q)

# 4. verdicts: re-parse every luna verdict, compare with the stored label
L = {(v['question_id'], v['sample']): v for v in read_jsonl(f'runs/{RUNS["micu"]}/judge_gpt-6-luna.jsonl')}
lk = collections.Counter((v['question_id'], v['sample']) for v in read_jsonl(f'runs/{RUNS["micu"]}/judge_gpt-6-luna.jsonl'))
report['luna_file_duplicates'] = sum(c > 1 for c in lk.values())
verdict = {}
for r in rows:
    k = (r['question_id'], r['sample'])
    if r['_run'] == 'micu':
        v = L.get(k)
        if v is None:
            issue('luna_missing', k); continue
        raw, stored = v['judge_raw'], v['correct']
    else:
        raw, stored = r['judge_raw'], r['correct']
    if raw is None:
        issue('luna_missing', k); continue
    if parse_label(raw) != stored:
        issue('luna_label_reparse_differs', k)
    try:
        lab = json.loads(raw[raw.index('{'): raw.rindex('}') + 1])['label']
        if lab not in ('CORRECT', 'WRONG'):
            issue('luna_label_unexpected', (k, lab))
    except Exception:
        issue('luna_raw_not_json', k)
    verdict[k] = stored
for o in json.loads(OVERRIDES.read_text()):
    verdict[(o['question_id'], o['sample'])] = o['correct']
# the verdict must be of THIS row's prediction: rebuild the luna cache key from the prediction
db = sqlite3.connect('.cache/llm.sqlite', timeout=60)
def lkey(msgs):
    return hashlib.sha256(json.dumps(['gpt-6-luna', msgs, {'temperature': 0.0, 'max_tokens': 256}],
                                     ensure_ascii=False, sort_keys=True).encode()).hexdigest()
stale = 0
for r in rows:
    q = Q[r['question_id']]
    hit = db.execute('SELECT v FROM c WHERE k=?', (lkey(judge_messages(q.category, q.question, q.reference, r['prediction'])),)).fetchone()
    raw = L[(r['question_id'], r['sample'])]['judge_raw'] if r['_run'] == 'micu' else r['judge_raw']
    if hit is None or json.loads(hit[0])['text'] != raw:
        stale += 1
        # luna samples at T=0, so identical predictions of one question share a cache key and only the
        # last call stays cached: a different stored text is expected there (conflicting labels -> overrides)
        same = any(o['prediction'] == r['prediction'] for o in rows if o['question_id'] == r['question_id'] and o is not r)
        issue('verdict_text_differs_shared_prediction' if same else 'verdict_not_of_this_prediction', (r['_run'], r['question_id'], r['sample']))
report['verdicts_checked_against_prediction'] = len(rows) - stale

# 5. distribution parity between sources (same settings -> same behaviour)
def stats(rs):
    h = [r for r in rs if r['hinted']]; p = [r for r in rs if not r['hinted']]
    return {'n': len(rs), 'acc': round(st.mean(bool(verdict.get((r['question_id'], r['sample']))) for r in rs), 3),
            'hinted_search': round(st.mean(bool(r['searches']) for r in h), 3) if h else None,
            'plain_search': round(st.mean(bool(r['searches']) for r in p), 3) if p else None,
            'answer_words_median': st.median(len(r['prediction'].split()) for r in rs),
            'steps_mean': round(st.mean(r['steps'] for r in rs), 2)}
report['by_source'] = {n: stats([r for r in rows if r['_run'] == n]) for n in RUNS}

# 6. answer content: leaks / junk the strict parser does not catch
JUNK = re.compile(r'(\bto=|<\|[a-z_]+\|>|```|functions\.|\bcommentary\b|^analysis|\{"reasoning")', re.I)
for r in rows:
    a = r['prediction']
    if JUNK.search(a):
        issue('junk_in_answer', (r['_run'], r['question_id'], r['sample'], JUNK.search(a).group(0)))
    if len(a.split()) > 300:
        issue('answer_over_300_words', (r['question_id'], r['sample']))
    if re.search(r'[一-鿿]', a):
        issue('cjk_in_answer', (r['question_id'], r['sample']))
    for x in r['messages']:
        if x['role'] == 'assistant':
            o = json.loads(x['content'])
            if JUNK.search(o['reasoning']):
                issue('junk_in_reasoning', (r['question_id'], r['sample']))
            if 'gold' in o['reasoning'].lower() or 'reference answer' in o['reasoning'].lower():
                issue('gold_mention_in_reasoning', (r['question_id'], r['sample']))
            if 'hint' in o['reasoning'].lower() or 'instructed to search' in o['reasoning'].lower():
                issue('hint_mention_in_reasoning', (r['question_id'], r['sample'], o['reasoning'][:120]))

# 7. SFT files: rebuild the selection and compare
tr, va = read_jsonl(SFT / 'train.jsonl'), read_jsonl(SFT / 'val.jsonl')
src = {(r['question_id'], r['sample']): r for r in rows}
for s in tr + va:
    o = src.get((s['question_id'], s['sample']))
    if o is None:
        issue('sft_row_without_source', s['question_id']); continue
    if s['messages'] != o['messages']:
        issue('sft_messages_differ_from_source', s['question_id'])
    if not verdict.get((s['question_id'], s['sample'])) or not o['format_ok']:
        issue('sft_kept_wrong_or_unclean', s['question_id'])
    if s['run'] != RUNS[o['_run']]:
        issue('sft_run_tag_wrong', s['question_id'])
kept_q = {s['question_id'] for s in tr + va}
for q in Q:
    has_ok = any(verdict.get((q, k)) and src[(q, k)]['format_ok'] for k in range(4))
    if has_ok != (q in kept_q):
        issue('sft_selection_inconsistent', q)
report['sft'] = {'train': len(tr), 'val': len(va), 'shared_questions': len({s['question_id'] for s in tr} & {s['question_id'] for s in va}),
                 'shared_scenarios': len({s['question_id'][:10] for s in tr} & {s['question_id'][:10] for s in va})}

# 8. quarantine / replaced files: nothing from them leaked back
moved = set()
for f in ['quarantine_prefix_bug.jsonl', 'quarantine_suspect.jsonl', 'trajectories.replaced_by_yan.jsonl']:
    p = Path(f'runs/{RUNS["micu"]}') / f
    for r in read_jsonl(p):
        moved.add((f, r['question_id'], r['sample'], r['messages'][-1]['content']))
leak = sum(1 for f, q, s, last in moved if src.get((q, s)) and src[(q, s)]['_run'] == 'micu' and src[(q, s)]['messages'][-1]['content'] == last)
report['quarantined_rows'] = len(moved); report['quarantined_rows_back_in_micu'] = leak
redo = {(l.split()[0], int(l.split()[1])) for l in Path(f'runs/{RUNS["micu"]}/yan_tail_keys.txt').read_text().splitlines() if l.strip()}
report['yan_tail_keys_all_in_yanredo'] = all(src.get(k, {}).get('_run') == 'yanredo' for k in redo)

report['issues'] = {k: {'count': len(v), 'examples': [str(x) for x in v[:5]]} for k, v in sorted(issues.items())}
Path('results/reader_cold_sol_audit.json').write_text(json.dumps(report, indent=2, ensure_ascii=False))
print(json.dumps(report, indent=1, ensure_ascii=False))
