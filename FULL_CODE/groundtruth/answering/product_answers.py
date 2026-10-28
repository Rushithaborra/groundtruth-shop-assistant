"""Replies about specific products: one product's facts, and a side-by-side comparison."""

from __future__ import annotations

import re

from ..catalogue import CatalogueIndex, Product
from ..text import format_inr
from ..understanding import Parsed
from .formatting import product_line, stock_text
from .response import ANSWER, REFUSE, Claims, Response

FACTS = frozenset({"price", "stock", "rating", "detail"})


def product_answer(index: CatalogueIndex, prod: Product, p: Parsed, lead: str | None = None) -> Response:
    """The facts the shopper asked about one product, each sentence cited; refuses if none is supported."""
    cite = f"[{prod.id}]"
    want = p.intents & FACTS
    # catalogue words that don't describe this product ("does <kettle> have bluetooth") are questions about it
    foreign = [t for t in p.spec.type_tokens if not index.has_text_token(prod.id, t)]
    facts_only = not want and bool(p.spec.desc_tokens or p.unknown or foreign)  # "is X waterproof?"
    defaults = not want and not facts_only
    sentences, supported, named = [], False, False
    claims = Claims()
    claims.cite(prod)

    def say(predicate: str) -> None:
        nonlocal named
        sentences.append(f"{'It' if named else f'The {prod.title}'} {predicate} {cite}.")
        named = True

    if lead:
        say(f"is the {lead} match")
    if "price" in want or defaults or lead:  # a "cheapest / costliest" pick always says the price
        say(f"is priced at {format_inr(prod.price)}")
        claims.price(prod)
        supported = True
    if "stock" in want or defaults:
        say(f"is {stock_text(prod)}" if prod.in_stock is not None else "has no stock status in the catalogue")
        if prod.in_stock is not None:
            claims.stock(prod)
        supported = True
    if "rating" in want or defaults:
        if prod.rating is not None:
            count = f" from {prod.rating_count} ratings" if prod.rating_count else ""
            say(f"is rated {prod.rating} stars{count}")
            claims.rating(prod)
            supported = True
        elif "rating" in want:
            sentences.append(f"The catalogue has no rating for the {prod.title} {cite}.")
    for key in relevant_keys([prod], p.detail_keys):
        values = prod.attr_values(key)
        if values:
            sentences.append(f"The {key} of the {prod.title} is {', '.join(values)} {cite}.")
            claims.attribute(prod, key)
            supported = True
        else:
            sentences.append(f"The catalogue does not list a {key} for the {prod.title} {cite}.")
    if "detail" in want or p.spec.desc_tokens or p.unknown or foreign:
        # an attribute question ("paani me chalega?") is answered by the attribute, not by echoing its other words
        unplaced = [] if p.detail_keys else p.unknown
        extra, ok = description_check(index, prod, p, foreign, unplaced)
        sentences += extra
        supported = supported or ok
    return Response(" ".join(sentences), ANSWER if supported else REFUSE, claims=claims)


def compare(index: CatalogueIndex, pids: list[str], p: Parsed) -> Response:
    """Up to four products side by side; names the cheaper / costlier / better-rated one when asked."""
    prods = [index.products[i] for i in pids][:4]
    want = (p.intents & {"price", "stock", "rating"}) or {"price"}
    claims = Claims()
    lines = ["Here is the comparison:"]
    lines += [product_line(x, claims, "price" in want, "rating" in want, "stock" in want) for x in prods]
    sort = p.spec.sort
    if sort in ("price_asc", "price_desc"):
        pick = (min if sort == "price_asc" else max)(prods, key=lambda x: x.price)
        if any(x.price != pick.price for x in prods):
            word = "cheaper" if sort == "price_asc" else "more expensive"
            lines.append(f"The {pick.title} is the {word} one at {format_inr(pick.price)} [{pick.id}].")
            claims.price(pick)
        else:
            lines.append(
                f"They are the same price, {format_inr(pick.price)} " + " ".join(f"[{x.id}]" for x in prods) + "."
            )
            for x in prods:
                claims.price(x)
    elif sort == "rating_desc" and (rated := [x for x in prods if x.rating is not None]):
        pick = max(rated, key=lambda x: x.rating)
        lines.append(f"The {pick.title} has the higher rating, {pick.rating} stars [{pick.id}].")
        claims.fact(pick, "rating", pick.rating)
    return Response("\n".join(lines), ANSWER, claims=claims)


def description_check(
    index: CatalogueIndex, prod: Product, p: Parsed, foreign: list[str] = (), unplaced: list[str] | None = None
) -> tuple[list[str], bool]:
    """Quote the description sentence for each asked-about word, or say the catalogue doesn't cover it."""
    out, ok = [], False
    in_description = index.desc_tokens[prod.id]
    unplaced = p.unknown if unplaced is None else unplaced
    for tok in dict.fromkeys(p.spec.desc_tokens + unplaced + list(foreign)):
        if tok in in_description:
            sentence = next(
                (s for s in re.split(r"(?<=[.!?])\s+", prod.description) if tok in s.lower()), prod.description
            )
            out.append(f'The description says: "{sentence}" [{prod.id}]')
            ok = True
        else:
            out.append(f'I don\'t have information about "{tok}" for the {prod.title} [{prod.id}].')
    return out, ok


def shown_details(products: list[Product], keys: list[str]) -> Response:
    """An attribute question about the products just listed ("which sizes?"), answered from each product's record."""
    lines, claims, supported = ["For the products I just showed:"], Claims(), False
    keys = relevant_keys(products, keys)
    for prod in products:
        parts = []
        for key in keys:
            values = prod.attr_values(key)
            if values:
                parts.append(f"{key} {', '.join(values)}")
                claims.attribute(prod, key)
                supported = True
            else:
                parts.append(f"{key} not listed")
        claims.cite(prod)
        lines.append(f"- {prod.title}: {'; '.join(parts)} [{prod.id}]")
    return Response("\n".join(lines), ANSWER if supported else REFUSE, claims=claims)


def relevant_keys(products: list[Product], keys: list[str]) -> list[str]:
    """The asked-about keys these products actually have ("size" also matches bat_size, bed_size, ...); if none of
    them has any, only the first, so the reply says once that the catalogue doesn't list it."""
    present = [k for k in keys if any(prod.attr_values(k) for prod in products)]
    return present or keys[:1]
