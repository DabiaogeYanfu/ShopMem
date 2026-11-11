"""Lexical answer metrics and retrieval metrics.

Tokenization: Chinese is scored per character (CJK characters, plus ASCII words and
numbers as single tokens so "68.5" or "PRESS-LINE-07-003" count once); English per
lower-cased word. F1 is SQuAD-style bag-of-tokens; BLEU-1 is clipped unigram precision
with brevity penalty.
"""
import math
import re
from collections import Counter

_ZH_TOKEN = re.compile(r'[一-鿿]|[A-Za-z0-9][A-Za-z0-9_\-.#%/:]*')
_EN_TOKEN = re.compile(r'[a-z0-9][a-z0-9_\-.#%/:]*')


def tokenize(text, lang):
    if lang == 'zh':
        return [t.rstrip('.') for t in _ZH_TOKEN.findall(text)]
    return [t.rstrip('.') for t in _EN_TOKEN.findall(text.lower())]


def f1(pred, gold, lang):
    p, g = Counter(tokenize(pred, lang)), Counter(tokenize(gold, lang))
    overlap = sum((p & g).values())
    if not overlap:
        return 0.0
    precision, recall = overlap / sum(p.values()), overlap / sum(g.values())
    return 2 * precision * recall / (precision + recall)


def bleu1(pred, gold, lang):
    p, g = tokenize(pred, lang), tokenize(gold, lang)
    if not p or not g:
        return 0.0
    clipped = sum((Counter(p) & Counter(g)).values())
    bp = 1.0 if len(p) > len(g) else math.exp(1 - len(g) / len(p))
    return bp * clipped / len(p)


def turn_recall(provenance, evidence):
    """Share of gold evidence turns present in the retrieved context (None if no evidence)."""
    if not evidence:
        return None
    got = set(provenance)
    return sum(e in got for e in evidence) / len(evidence)


def event_recall(provenance, required_events, event_turns):
    """Share of required events with at least one source turn in the retrieved context."""
    if not required_events:
        return None
    got = set(provenance)
    return sum(any(t in got for t in event_turns.get(e, ())) for e in required_events) / len(required_events)
