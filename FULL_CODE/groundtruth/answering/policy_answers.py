"""Policy answers: the matching sentences from the policy text, each cited to its section."""

from __future__ import annotations

from ..policy import Section, best_sentences
from ..understanding import Parsed
from .response import ANSWER, REFUSE, Claims, Response


def policy_answer(policy: list[Section], semantic, p: Parsed, max_sentences: int) -> Response:
    """Quote the best-matching policy sentences; the embedding fallback (if any) only runs when keywords miss."""
    found = best_sentences(policy, p.keywords, max_sentences)
    if not found and semantic is not None:
        hit = semantic.policy_sentence(p.raw)
        if hit:
            sec, sentence, _ = hit
            return Response(f"{sentence} [policy:{sec.id}]", ANSWER, path="semantic", claims=cited_section(sec.id))
    if not found:
        return Response("I don't have that in our policy information.", REFUSE)
    sec, sentences = found
    return Response(" ".join(f"{s} [policy:{sec.id}]" for s in sentences), ANSWER, claims=cited_section(sec.id))


def cited_section(section_id: str) -> Claims:
    claims = Claims()
    claims.section(section_id)
    return claims
