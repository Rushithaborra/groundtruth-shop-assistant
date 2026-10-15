"""Policy document: split plain text into cited sections and answer questions with extracted sentences."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .text import content_tokens, split_sentences, tokenize


@dataclass
class Section:
    id: str
    heading: str
    body: str


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


def best_sentences(sections: list[Section], keywords: list[str], limit: int = 2):
    """Return (section, [sentences]) for the best-matching section, or None when nothing fits."""
    wanted = set(keywords)
    best = None
    for sec in sections:
        heading_hit = bool(wanted & set(tokenize(sec.heading)))
        scored = []
        for sent in split_sentences(sec.body):
            overlap = len(wanted & set(content_tokens(sent)))
            score = overlap + (1 if heading_hit else 0)
            if overlap and score >= 2:
                scored.append((score, sent))
        if scored:
            top = max(s for s, _ in scored)
            if best is None or top > best[0]:
                keep = sorted(range(len(scored)), key=lambda i: -scored[i][0])[:limit]
                picked = [scored[i][1] for i in sorted(keep) if scored[i][0] >= top - 1]
                best = (top, sec, picked)
    return (best[1], best[2]) if best else None
