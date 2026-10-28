"""Replies that show several products: a ranked listing, or a clarifying question when "the" product is ambiguous."""

from __future__ import annotations

from ..catalogue import SIZE_KEY, CatalogueIndex, Product, Spec
from ..understanding import Parsed
from .formatting import product_line
from .response import ANSWER, CLARIFY, Response


def listing(hits: list[Product], p: Parsed, spec: Spec, relaxed: bool, list_limit: int) -> Response:
    """The top matches, cited, with notes on words read differently or not verified."""
    shown = hits[:list_limit]
    kind = {"price_asc": "lowest-priced", "price_desc": "highest-priced", "rating_desc": "highest-rated"}.get(
        spec.sort, "best"
    )
    lines = [f"Here are the {kind} matches:"] + [product_line(h) for h in shown]
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
    return Response("\n".join(lines), ANSWER)


def clarify(index: CatalogueIndex, hits: list[Product], clarify_limit: int) -> Response:
    """Ask which product the shopper means, listing the attributes that tell the candidates apart (no prices)."""
    lowered = index.attr_lower
    keys = dict.fromkeys(k for h in hits for k in lowered[h.id] if k != SIZE_KEY)
    varying = [k for k in keys if len({lowered[h.id].get(k) for h in hits}) > 1][:3]
    lines = ["I found more than one match, so I don't want to guess. Which one do you mean?"]
    for h in hits[:clarify_limit]:
        detail = ", ".join(v for k in varying for v in h.attr_values(k)[:1])
        lines.append(f"- {h.title}{f' ({detail})' if detail else ''} [{h.id}]")
    if len(hits) > clarify_limit:
        lines.append("There are more options too.")
    if varying:
        lines.append("You can tell me the " + " or ".join(varying) + " to pick one.")
    return Response("\n".join(lines), CLARIFY)
