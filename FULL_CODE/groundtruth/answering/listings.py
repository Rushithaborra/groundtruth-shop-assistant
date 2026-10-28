"""Replies that show several products: a ranked listing, or a clarifying question when "the" product is ambiguous."""

from __future__ import annotations

from ..catalogue import SIZE_KEY, CatalogueIndex, Product, Spec
from ..understanding import Parsed
from .formatting import product_line
from .response import ANSWER, CLARIFY, Claims, Response


def listing(hits: list[Product], p: Parsed, spec: Spec, relaxed: bool, list_limit: int) -> Response:
    """The top matches, cited, with notes on words read differently or not verified."""
    shown = hits[:list_limit]
    kind = {"price_asc": "lowest-priced", "price_desc": "highest-priced", "rating_desc": "highest-rated"}.get(
        spec.sort, "best"
    )
    claims = Claims()
    lines = [f"Here are the {kind} matches:"] + [product_line(h, claims) for h in shown]
    if len(hits) > len(shown):
        lines.append("There are more matches; tell me a brand, colour or budget to narrow them down.")
    if p.mapped:
        lines.insert(0, "(I read " + ", ".join(f'"{w}" as "{t}"' for w, t in p.mapped) + ".)")
    if relaxed and spec.desc_tokens:
        lines.append(
            f'I could not confirm "{" ".join(spec.desc_tokens)}" in the product descriptions, '
            "so these are the closest matches."
        )
    if p.unknown:
        lines.append(f'I can\'t verify "{" ".join(p.unknown)}" from the catalogue.')
    return Response("\n".join(lines), ANSWER, claims=claims)


def clarify(index: CatalogueIndex, hits: list[Product], clarify_limit: int) -> Response:
    """Ask which product the shopper means, listing the attributes that tell the candidates apart (no prices)."""
    lowered = index.attr_lower
    keys = dict.fromkeys(k for h in hits for k in lowered[h.id] if k != SIZE_KEY)
    varying = [k for k in keys if len({lowered[h.id].get(k) for h in hits}) > 1][:3]
    lines, claims = ["I found more than one match, so I don't want to guess. Which one do you mean?"], Claims()
    for h in hits[:clarify_limit]:
        detail = ", ".join(v for k in varying for v in h.attr_values(k)[:1])
        lines.append(f"- {h.title}{f' ({detail})' if detail else ''} [{h.id}]")
        claims.cite(h)
        for key in varying:  # the reply shows only the first value of a list attribute, so only that is claimed
            value = h.attributes.get(key)
            if isinstance(value, (list, tuple)):
                value = value[0] if value else None
            if value is not None:
                claims.fact(h, h.source_key(key), value)
    if len(hits) > clarify_limit:
        lines.append("There are more options too.")
    if varying:
        lines.append("You can tell me the " + " or ".join(varying) + " to pick one.")
    return Response("\n".join(lines), CLARIFY, claims=claims)


def choice(index: CatalogueIndex, p: Parsed, per_kind: int) -> Response:
    """ "tshirt or shirt, which is better?": the catalogue can't judge "better", so show some of each and ask what
    matters, rather than guessing."""
    lines, claims = (
        ["Both are in the catalogue, and which is better depends on what you need. A few of each:"],
        Claims(),
    )
    for kind in p.choice:
        hits, _ = index.search(Spec(type_tokens=[kind]), p.all_tokens)
        for h in hits[: max(1, per_kind - 1)]:
            lines.append(f"- {h.title} [{h.id}]")
            claims.cite(h)
    lines.append("Tell me your budget, size, material or what it's for, and I'll narrow it down.")
    return Response("\n".join(lines), CLARIFY, claims=claims)
