"""Policy answers: the matching sentences from the policy text, each cited to its section."""

from __future__ import annotations

from ..policy import STRONG, Section, best_sentences
from ..understanding import Parsed
from .response import ANSWER, REFUSE, Claims, Response


def policy_answer(
    policy: list[Section], semantic, p: Parsed, max_sentences: int, strong: frozenset = STRONG
) -> Response:
    """Quote the best-matching policy sentences; the embedding fallback (if any) only runs when keywords miss."""
    found = best_sentences(policy, p.keywords, max_sentences, strong)
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


SPOKEN = {"sameday": "same day"}  # words joined by the parser, shown as the shopper wrote them
# What a delivery-day question can still be answered with: the policy's delivery timelines.
TIME_TOPIC = ("timeline", "business", "day", "ship")


def policy_silent(policy: list[Section], p: Parsed, silent: list[str], max_sentences: int) -> Response:
    """The policy never mentions what was asked ("sunday"): say so, then quote what it does say on the topic."""
    words = " or ".join(f'"{SPOKEN.get(w, w)}"' for w in silent)
    text = f"The store policy doesn't say anything about {words}, so I can't promise it."
    rest = [k for k in p.keywords if k not in silent]
    on_topic = [sec for sec in policy if sec.heading_tokens & set(rest)] or policy  # delivery -> "Delivery ..."
    found = best_sentences(on_topic, rest + list(TIME_TOPIC), max_sentences)
    if not found:
        return Response(text, REFUSE)
    sec, sentences = found
    text += " What it does say: " + " ".join(f"{s} [policy:{sec.id}]" for s in sentences)
    return Response(text, REFUSE, claims=cited_section(sec.id))
