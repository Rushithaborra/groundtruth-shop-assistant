"""Refusals: say "I don't have that" and, where the catalogue allows, name the closest cited alternative."""

from __future__ import annotations

from ..catalogue import CatalogueIndex, Spec
from ..text import EXTERNAL_DISPLAY, format_inr
from ..understanding import Parsed
from .formatting import product_line
from .response import ANSWER, REFUSE, Response


def describe(spec: Spec) -> str:
    """The shopper's request in their own terms, e.g. \"cotton kurta\" or \"blue running shoe\"."""
    words = list(dict.fromkeys([str(v).lower() for v in spec.filters.values()] + spec.type_tokens + spec.desc_tokens))
    return " ".join(words)


def no_match(index: CatalogueIndex, spec: Spec, p: Parsed, list_limit: int) -> Response:
    """Nothing satisfies every constraint: refuse, naming the nearest product outside the budget or filters."""
    base = "I don't have that in the catalogue."
    if spec.price_max is not None or spec.price_min is not None:
        looser = spec.copy()
        looser.price_min = looser.price_max = None
        alt, _ = index.search(looser, p.all_tokens)
        if alt:
            lowest = spec.price_max is not None
            pick = (min if lowest else max)(alt, key=lambda x: x.price)
            what = describe(spec)
            if what:
                if spec.price_min is not None and spec.price_max is not None:
                    budget = f"between {format_inr(spec.price_min)} and {format_inr(spec.price_max)}"
                elif lowest:
                    budget = f"under {format_inr(spec.price_max)}"
                else:
                    budget = f"over {format_inr(spec.price_min)}"
                return Response(
                    f"No {what} {budget} in the catalogue; the "
                    f"{'cheapest' if lowest else 'most expensive'} {what} I have is {pick.title} "
                    f"at {format_inr(pick.price)} [{pick.id}].",
                    REFUSE,
                )
            return Response(
                f"{base} The {'lowest' if lowest else 'highest'}-priced option that matches otherwise "
                f"is {pick.title} at {format_inr(pick.price)} [{pick.id}].",
                REFUSE,
            )
    if spec.filters or spec.size:
        looser = spec.copy()
        looser.filters, looser.size = {}, None
        alt, _ = index.search(looser, p.all_tokens)
        if alt:
            lines = [f"{base} Similar products without that exact combination:"]
            lines += [f"- {h.title} [{h.id}]" for h in alt[:list_limit]]
            return Response("\n".join(lines), REFUSE)
    return Response(base, REFUSE)


def alternatives(index: CatalogueIndex, spec: Spec, p: Parsed, header: str, list_limit: int) -> list[str]:
    hits, _ = index.search(spec, p.all_tokens)
    if not hits:
        return []
    return [header] + [product_line(h) for h in hits[:list_limit]]


def _brand_names(p: Parsed) -> str:
    return " ".join(EXTERNAL_DISPLAY.get(w, w.capitalize()) for w in dict.fromkeys(p.external))


def external(index: CatalogueIndex, p: Parsed, list_limit: int) -> Response:
    """A brand or product the shop doesn't carry ("nike shoes", "iphone")."""
    lines = [f"I don't have {_brand_names(p)} products in the catalogue."]
    if p.spec.type_tokens:
        other = p.spec.copy()
        other.desc_tokens = []
        lines += alternatives(index, other, p, "Similar products from brands I do carry:", list_limit)
    return Response("\n".join(lines), REFUSE)


def external_policy(p: Parsed, general: Response) -> Response:
    """A policy question about a brand the shop doesn't carry; adds the general policy when there is one."""
    text = f"I don't have {_brand_names(p)} products in the catalogue, so I can't say what applies to one."
    if general.action == ANSWER:
        text += f" The general policy is: {general.text}"
    return Response(text, REFUSE)


def unknown_refusal(index: CatalogueIndex, p: Parsed, list_limit: int) -> Response:
    """Product words the catalogue doesn't contain; offers a related type the shop does sell, if one is known."""
    lines = [f'I don\'t have anything in the catalogue matching "{" ".join(p.unknown)}".']
    for alt in dict.fromkeys(p.alternatives):
        lines += alternatives(index, Spec(type_tokens=[alt]), p, "The closest products I do have:", list_limit)
        break
    return Response("\n".join(lines), REFUSE)
