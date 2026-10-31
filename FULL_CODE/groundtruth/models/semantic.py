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
import logging
import math
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, Sequence

from ..catalogue import KIND_KEY, head_nouns
from ..policy import Section
from .calls import ModelCallError

if TYPE_CHECKING:
    from ..catalogue import CatalogueIndex
    from ..understanding import Parsed

logger = logging.getLogger(__name__)


class Embedder(Protocol):
    """Anything that turns texts into vectors: the local embedding model, or a fake one in tests."""
    name: str

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        """Embed texts as vectors."""
        ...


def unit(v: Sequence[float]) -> list[float]:
    """The vector scaled to length 1."""
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def dot(a: Sequence[float], b: Sequence[float]) -> float:
    """Dot product (cosine similarity for unit vectors)."""
    return sum(x * y for x, y in zip(a, b))


@dataclass
class Match:
    """A word the shopper used, the catalogue term it was matched to, and how similar the two are."""
    word: str
    target: str
    score: float


def type_words(index: CatalogueIndex) -> list[str]:
    """Catalogue words that name a kind of product: concentrated in one category, frequent, and not a brand.

    Model names ("Flex", "Apex") and brands spread across categories or are excluded explicitly, so the fallback
    can only map a shopper word onto a product type, never onto a brand, model, colour or material.
    """
    # brands, colours, ...; not the kinds of product themselves ("Running Shoes"), or no kind word could be a type
    attr_words = {t for key, vals in index.attr_values.items() if key != KIND_KEY for toks in vals for t in toks}
    words = []
    for tok, pids in index._text_postings.items():
        if len(pids) < max(3, len(index.products) // 500) or tok in attr_words or tok.isdigit() or len(tok) < 3:
            continue
        cats = Counter(index.products[p].category for p in pids)
        if cats.most_common(1)[0][1] / len(pids) >= 0.9:
            words.append(tok)
    return sorted(words)


def embedding_targets(index: CatalogueIndex) -> list[str]:
    """The words the embedding fallback may map a shopper word onto: the product types that name what a product is.

    `type_words` also holds category names ("apparel", "home") and describing words ("wireless"). A shopper word that
    lands near one of those would pull in a whole category, or every wireless product, instead of being refused, so
    only the nouns that end product titles and kinds ("shoe", "earbud", "bank") are kept.
    """
    nouns = head_nouns(index)
    return [w for w in type_words(index) if w in nouns]


class SemanticFallback:
    """Optional: matches words the rules couldn't place to catalogue terms or policy sections by embedding
    similarity. Off by default: measured, it lowered the dev score."""
    def __init__(self, index: CatalogueIndex, policy: list[Section], embedder: Embedder, map_threshold: float,
                 alt_threshold: float | None = None, policy_threshold: float | None = None,
                 cache_dir: str | Path | None = None) -> None:
        self.embedder = embedder
        self.map_threshold = map_threshold
        self.alt_threshold = alt_threshold if alt_threshold is not None else map_threshold
        self.policy_threshold = policy_threshold
        self.targets = embedding_targets(index)
        self.policy_units = [(sec, sent) for sec in policy for sent, _ in sec.sentences]
        texts = self.targets + [s for _, s in self.policy_units]
        vecs = self._cached(texts, cache_dir)
        self.target_vecs = vecs[: len(self.targets)]
        self.policy_vecs = vecs[len(self.targets):]
        self._memo: dict[str, Match | None] = {}

    def _cached(self, texts: list[str], cache_dir: str | Path | None) -> list[list[float]]:
        """Embeddings for these texts, read from or written to the cache folder when one is given.

        A cache file that is cut off, unreadable or the wrong size (a killed start-up, a disk filling up) is rebuilt
        rather than stopping the assistant, and a new file is written whole or not at all.
        """
        if not cache_dir:
            return self.embedder.encode(texts)
        key = hashlib.sha1(json.dumps([self.embedder.name, texts]).encode()).hexdigest()[:16]
        path = Path(cache_dir) / f"embeddings-{key}.json"
        cached = _read_cache(path, len(texts))
        if cached is not None:
            return cached
        vecs = self.embedder.encode(texts)
        _write_cache(path, vecs)
        return vecs

    def nearest_type(self, word: str) -> Match | None:
        """The catalogue product-type word closest to a shopper word, with its similarity."""
        if word not in self._memo:
            if not self.targets:
                self._memo[word] = None
            else:
                v = self._embed(word)
                if v is None:
                    return None  # the model failed: not remembered, so the next question tries again
                score, target = max((dot(v, t), w) for t, w in zip(self.target_vecs, self.targets))
                self._memo[word] = Match(word, target, score)
        return self._memo[word]

    def _embed(self, text: str) -> list[float] | None:
        """One text as a vector, or None when the model times out, is busy or fails: the caller then does what the
        rule-based path would have done, as with every other model call."""
        try:
            return self.embedder.encode([text])[0]
        except ModelCallError as exc:
            logger.warning("embedding fallback skipped: %s", exc)
            return None

    def resolve(self, parsed: Parsed) -> bool:
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
        """The policy sentence closest to the query, if it clears the calibrated threshold."""
        if self.policy_threshold is None or not self.policy_units:
            return None
        v = self._embed(query)
        if v is None:
            return None
        score, i = max((dot(v, p), i) for i, p in enumerate(self.policy_vecs))
        if score < self.policy_threshold:
            return None
        sec, sent = self.policy_units[i]
        return sec, sent, score


def _read_cache(path: Path, expected: int) -> list[list[float]] | None:
    """The cached vectors, or None when there is no usable cache file (missing, cut off, or the wrong size)."""
    try:
        vecs = json.loads(path.read_text())
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        logger.warning("embedding cache %s is unreadable (%s); rebuilding it", path, exc)
        return None
    if not isinstance(vecs, list) or len(vecs) != expected:
        logger.warning("embedding cache %s does not match the catalogue and policy; rebuilding it", path)
        return None
    return vecs


def _write_cache(path: Path, vecs: list[list[float]]) -> None:
    """Save the vectors through a temporary file, so a start-up killed half-way never leaves a cut-off cache. A cache
    that cannot be written costs only the next start-up's time."""
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(vecs))
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("embedding cache %s not saved: %s", path, exc)
        tmp.unlink(missing_ok=True)


def load_config(path: str | Path) -> dict | None:
    """Calibrated thresholds from main.py calibrate, or None if not calibrated yet."""
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else None
