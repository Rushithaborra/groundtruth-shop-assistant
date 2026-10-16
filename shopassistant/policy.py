"""Policy document: split plain text into cited sections and answer questions with extracted sentences."""
from __future__ import annotations

import heapq
import re
from dataclasses import dataclass, field
from pathlib import Path

from .text import content_tokens, split_sentences, tokenize


@dataclass
class Section:
    id: str
    heading: str
    body: str
    sentences: list[tuple[str, frozenset[str]]] = field(default_factory=list)  # (sentence, content tokens)
    heading_tokens: frozenset[str] = frozenset()

    def __post_init__(self):
        self.sentences = [(s, frozenset(content_tokens(s))) for s in split_sentences(self.body)]
        self.heading_tokens = frozenset(tokenize(self.heading))


def _slug(heading: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")


def load_policy(path: str | Path) -> list[Section]:
    text = Path(path).read_text(encoding="utf-8")
    sections, seen = [], {}
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [ln.strip() for ln in block.strip().splitlines() if ln.strip()]
        if not lines:
            continue
        first = lines[0].lstrip("# ").rstrip(":")
        is_heading = len(lines) > 1 and len(first.split()) <= 6 and not first.endswith(".")
        heading, body = (first, " ".join(lines[1:])) if is_heading else ("General", " ".join(lines))
        sid = _slug(heading)
        seen[sid] = seen.get(sid, 0) + 1
        if seen[sid] > 1:
            sid = f"{sid}-{seen[sid]}"
        sections.append(Section(sid, heading, body))
    return sections


# A single one of these words is enough to identify a policy sentence ("refund kab milega", "cancel order").
STRONG = frozenset({"refund", "exchange", "cancel", "warranty", "guarantee", "cash", "ship", "return", "dispatch"})


def best_sentences(sections: list[Section], keywords: list[str], limit: int = 2):
    """Return (section, [sentences]) for the best-matching section, or None when nothing fits."""
    wanted = frozenset(keywords)
    best = None
    for sec in sections:
        bonus = bool(wanted & sec.heading_tokens)
        scored = [(len(wanted & toks) + bonus, i, sent) for i, (sent, toks) in enumerate(sec.sentences)
                  if wanted & toks and (len(wanted & toks) + bonus >= 2 or wanted & toks & STRONG)]
        if scored and (best is None or max(scored)[0] > best[0]):
            top = max(sc for sc, _, _ in scored)
            keep = sorted(heapq.nlargest(limit, scored, key=lambda t: (t[0], -t[1])), key=lambda t: t[1])
            best = (top, sec, [sent for sc, _, sent in keep if sc >= top - 1])
    return (best[1], best[2]) if best else None
