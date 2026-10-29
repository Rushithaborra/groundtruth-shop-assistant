"""Refusals: say "I don't have that" and, where the catalogue allows, name the closest cited alternative."""

from __future__ import annotations

from ..catalogue import CatalogueIndex, Spec
from ..text import EXTERNAL_DISPLAY, format_inr
from ..understanding import Parsed
from .formatting import product_line
from .response import ANSWER, REFUSE, Claims, Response


def describe(spec: Spec) -> str:
    """The shopper's request in their own terms, e.g. \"cotton kurta\" or \"blue running shoe\"."""
    words = list(dict.fromkeys([str(v).lower() for v in spec.filters.values()] + spec.type_tokens + spec.desc_tokens))
    return " ".join(words)


def no_match(index: CatalogueIndex, spec: Spec, p: Parsed, list_limit: int) -> Response:
    """Nothing satisfies every constraint: refuse, naming the nearest product outside the budget or filters."""
    base = "I don't have that in the catalogue."
    return (
        _outside_budget(index, spec, p, base)
        or _without_filters(index, spec, p, list_limit, base)
        or Response(base, REFUSE)
    )


def _outside_budget(index: CatalogueIndex, spec: Spec, p: Parsed, base: str) -> Response | None:
    """The cheapest (or dearest) product that fits except for the budget, when the budget is what ruled things out."""
    if spec.price_max is None and spec.price_min is None:
        return None
    looser = spec.copy()
    looser.price_min = looser.price_max = None
    alt, _ = index.search(looser, p.all_tokens)
    if not alt:
        return None
    lowest = spec.price_max is not None
    pick = (min if lowest else max)(alt, key=lambda x: x.price)
    claims = Claims()
    claims.price(pick)
    what = describe(spec)
    if what:
        return Response(
            f"No {what} {_budget_phrase(spec)} in the catalogue; the "
            f"{'cheapest' if lowest else 'most expensive'} {what} I have is {pick.title} "
            f"at {format_inr(pick.price)} [{pick.id}].",
            REFUSE,
            claims=claims,
        )
    return Response(
        f"{base} The {'lowest' if lowest else 'highest'}-priced option that matches otherwise "
        f"is {pick.title} at {format_inr(pick.price)} [{pick.id}].",
        REFUSE,
        claims=claims,
    )


def _budget_phrase(spec: Spec) -> str:
    """The shopper's budget in words: "under ₹500", "over ₹500" or "between ₹500 and ₹900"."""
    if spec.price_min is not None and spec.price_max is not None:
        return f"between {format_inr(spec.price_min)} and {format_inr(spec.price_max)}"
    if spec.price_max is not None:
        return f"under {format_inr(spec.price_max)}"
    return f"over {format_inr(spec.price_min)}"


def _without_filters(index: CatalogueIndex, spec: Spec, p: Parsed, list_limit: int, base: str) -> Response | None:
    """Products of the right kind without the asked-for filter or size, when that combination is what's missing."""
    if not (spec.filters or spec.size):
        return None
    looser = spec.copy()
    looser.filters, looser.size = {}, None
    alt, _ = index.search(looser, p.all_tokens)
    if not alt:
        return None
    lines = [f"{base} Similar products without that exact combination:"]
    claims = Claims()
    for h in alt[:list_limit]:
        lines.append(f"- {h.title} [{h.id}]")
        claims.cite(h)
    return Response("\n".join(lines), REFUSE, claims=claims)


def alternatives(
    index: CatalogueIndex, spec: Spec, p: Parsed, header: str, list_limit: int, claims: Claims
) -> list[str]:
    """Cited lines for the closest products a refusal can offer, with a header."""
    hits, _ = index.search(spec, p.all_tokens)
    if not hits:
        return []
    return [header] + [product_line(h, claims) for h in hits[:list_limit]]


def _brand_names(p: Parsed) -> str:
    """The brands the shop doesn't carry, as named in a reply."""
    return " ".join(EXTERNAL_DISPLAY.get(w, w.capitalize()) for w in dict.fromkeys(p.external))


def external(index: CatalogueIndex, p: Parsed, list_limit: int) -> Response:
    """A brand or product the shop doesn't carry ("nike shoes", "iphone")."""
    lines, claims = [f"I don't have {_brand_names(p)} products in the catalogue."], Claims()
    if p.spec.type_tokens:
        other = p.spec.copy()
        other.desc_tokens = []
        lines += alternatives(index, other, p, "Similar products from brands I do carry:", list_limit, claims)
    return Response("\n".join(lines), REFUSE, claims=claims)


def external_policy(p: Parsed, general: Response) -> Response:
    """A policy question about a brand the shop doesn't carry; adds the general policy when there is one."""
    text = f"I don't have {_brand_names(p)} products in the catalogue, so I can't say what applies to one."
    claims = Claims()
    if general.action == ANSWER:
        text += f" The general policy is: {general.text}"
        claims.merge(general.claims)
    return Response(text, REFUSE, claims=claims)


def unknown_refusal(index: CatalogueIndex, p: Parsed, list_limit: int) -> Response:
    """Product words the catalogue doesn't contain; offers a related type the shop does sell, if one is known."""
    lines, claims = [f'I don\'t have anything in the catalogue matching "{" ".join(p.unknown)}".'], Claims()
    for alt in dict.fromkeys(p.alternatives):
        header = "The closest products I do have:"
        lines += alternatives(index, Spec(type_tokens=[alt]), p, header, list_limit, claims)
        break
    return Response("\n".join(lines), REFUSE, claims=claims)
