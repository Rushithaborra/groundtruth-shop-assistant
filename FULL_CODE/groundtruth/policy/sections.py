"""Policy document: split it into cited sections and answer questions with extracted sentences.

Two layouts are read. Markdown with numbered sub-sections (`### 1.2 Free returns`) gives one section per
sub-section, cited by its number ("1.2"). Plain text is split on blank lines; a short first line becomes the
heading, and its slug is the citation id ("returns").
"""

from __future__ import annotations

import heapq
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..errors import DataFileError
from ..text import content_tokens, split_sentences, tokenize


@dataclass
class Section:
    id: str
    heading: str
    body: str
    sentences: list[tuple[str, frozenset[str]]] = field(default_factory=list)  # (sentence, content tokens)
    heading_tokens: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        self.sentences = [(s, frozenset(content_tokens(s))) for s in split_sentences(self.body)]
        self.heading_tokens = frozenset(tokenize(self.heading))


def _slug(heading: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")


_SUBSECTION = re.compile(r"^###\s+(\d+\.\d+)\s+(.+?)\s*$", re.MULTILINE)
_MARKUP = re.compile(r"\*\*|__|`")


def load_policy(path: str | Path) -> list[Section]:
    """Policy sections from Markdown with numbered sub-sections, or from plain text split on blank lines."""
    path = Path(path)
    if not path.is_file():
        raise DataFileError(f"policy file not found: {path}")
    text = path.read_text(encoding="utf-8")
    if _SUBSECTION.search(text):
        return _numbered_sections(text)
    return _plain_sections(text)


def _numbered_sections(text: str) -> list[Section]:
    """One section per `### N.M Title` sub-section; Markdown emphasis is removed from the quoted text."""
    matches = list(_SUBSECTION.finditer(text))
    sections = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        lines = [ln.strip() for ln in text[m.end() : end].splitlines()]
        body = " ".join(ln for ln in lines if ln and not ln.startswith("#"))
        sections.append(Section(m.group(1), _MARKUP.sub("", m.group(2)), _MARKUP.sub("", body)))
    return sections


def _plain_sections(text: str) -> list[Section]:
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


def best_sentences(
    sections: list[Section], keywords: list[str], limit: int, strong: frozenset[str] = STRONG
) -> tuple[Section, list[str]] | None:
    """Return (section, [sentences]) for the best-matching section, or None when nothing fits.

    A sentence matches on two shared words, or on one word from `strong` (words that alone identify a policy topic).
    """
    wanted = frozenset(keywords)
    best = None
    for sec in sections:
        bonus = bool(wanted & sec.heading_tokens)
        scored = [
            (len(wanted & toks) + bonus, i, sent)
            for i, (sent, toks) in enumerate(sec.sentences)
            if wanted & toks and (len(wanted & toks) + bonus >= 2 or wanted & toks & strong)
        ]
        if scored and (best is None or max(scored)[0] > best[0]):
            top = max(sc for sc, _, _ in scored)
            keep = heapq.nlargest(limit, scored, key=lambda t: (t[0], -t[1]))  # most relevant sentence first
            best = (top, sec, [sent for sc, _, sent in keep if sc >= top - 1])
    return (best[1], best[2]) if best else None
