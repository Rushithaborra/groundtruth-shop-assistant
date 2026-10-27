"""Embedding fallback for words the catalogue's own vocabulary doesn't contain.

It runs only on the miss path (after the rule-based parser leaves words unrecognised) and only changes how the query
is *understood*: a shopper word is mapped to a catalogue product-type word ("jogging" -> "running") or a policy
sentence is retrieved. Every reply is still assembled from catalogue fields and policy text with citations, so the
fallback cannot introduce a price or a fact.

Two thresholds, both calibrated with `python main.py calibrate` against the provided model:
    map_threshold  at or above: treat the word as the catalogue word and search for it
    alt_threshold  at or above (but below map): refuse, but offer the catalogue word's products as an alternative
Without a calibration file the fallback stays off: an uncalibrated threshold is how "iPhone" turns into "power bank".
"""
from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

from ..policy import Section


class Embedder(Protocol):
    name: str

    def encode(self, texts: Sequence[str]) -> list[list[float]]: ...


def unit(v: Sequence[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


@dataclass
class Match:
    word: str
    target: str
    score: float


def type_words(index) -> list[str]:
    """Catalogue words that name a kind of product: concentrated in one category, frequent, and not a brand.

    Model names ("Flex", "Apex") and brands spread across categories or are excluded explicitly, so the fallback
    can only map a shopper word onto a product type, never onto a brand, model, colour or material.
    """
    attr_words = {t for vals in index.attr_values.values() for toks in vals for t in toks}  # brands, colours, ...
    words = []
    for tok, pids in index._text_postings.items():
        if len(pids) < max(3, len(index.products) // 500) or tok in attr_words or tok.isdigit() or len(tok) < 3:
            continue
        cats = Counter(index.products[p].category for p in pids)
        if cats.most_common(1)[0][1] / len(pids) >= 0.9:
            words.append(tok)
    return sorted(words)


class SemanticFallback:
    def __init__(self, index, policy: list[Section], embedder: Embedder, map_threshold: float,
                 alt_threshold: float | None = None, policy_threshold: float | None = None,
                 cache_dir: str | Path | None = None):
        self.embedder = embedder
        self.map_threshold = map_threshold
        self.alt_threshold = alt_threshold if alt_threshold is not None else map_threshold
        self.policy_threshold = policy_threshold
        self.targets = type_words(index)
        self.policy_units = [(sec, sent) for sec in policy for sent, _ in sec.sentences]
        texts = self.targets + [s for _, s in self.policy_units]
        vecs = self._cached(texts, cache_dir)
        self.target_vecs = vecs[: len(self.targets)]
        self.policy_vecs = vecs[len(self.targets):]
        self._memo: dict[str, Match | None] = {}

    def _cached(self, texts: list[str], cache_dir) -> list[list[float]]:
        if not cache_dir:
            return self.embedder.encode(texts)
        key = hashlib.sha1(json.dumps([self.embedder.name, texts]).encode()).hexdigest()[:16]
        path = Path(cache_dir) / f"embeddings-{key}.json"
        if path.exists():
            return json.loads(path.read_text())
        vecs = self.embedder.encode(texts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(vecs))
        return vecs

    def nearest_type(self, word: str) -> Match | None:
        if word not in self._memo:
            if not self.targets:
                self._memo[word] = None
            else:
                v = self.embedder.encode([word])[0]
                score, target = max((dot(v, t), w) for t, w in zip(self.target_vecs, self.targets))
                self._memo[word] = Match(word, target, score)
        return self._memo[word]

    def resolve(self, parsed) -> bool:
        """Map unknown product words in place. Returns True if the parse changed."""
        changed = False
        for word in list(parsed.unknown):
            if word in parsed.external:
                continue  # a brand the shop doesn't carry is never mapped onto something it does
            m = self.nearest_type(word)
            if m is None or m.score < self.alt_threshold:
                continue
            if m.score >= self.map_threshold:
                parsed.unknown.remove(word)
                if m.target not in parsed.spec.type_tokens:
                    parsed.spec.type_tokens.append(m.target)
                parsed.mapped.append((word, m.target))
            elif m.target not in parsed.alternatives:
                parsed.alternatives.append(m.target)
            changed = True
        return changed

    def policy_sentence(self, query: str) -> tuple[Section, str, float] | None:
        if self.policy_threshold is None or not self.policy_units:
            return None
        v = self.embedder.encode([query])[0]
        score, i = max((dot(v, p), i) for i, p in enumerate(self.policy_vecs))
        if score < self.policy_threshold:
            return None
        sec, sent = self.policy_units[i]
        return sec, sent, score


def load_config(path: str | Path) -> dict | None:
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else None
