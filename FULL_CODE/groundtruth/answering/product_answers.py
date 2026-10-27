"""Replies about specific products: one product's facts, and a side-by-side comparison."""
from __future__ import annotations

import re

from ..catalogue import CatalogueIndex, Product
from ..text import format_inr
from ..understanding import Parsed
from .formatting import product_line, stock_text
from .response import ANSWER, REFUSE, Response

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

    def say(predicate: str) -> None:
        nonlocal named
        sentences.append(f"{'It' if named else f'The {prod.title}'} {predicate} {cite}.")
        named = True

    if lead:
        say(f"is the {lead} match")
    if "price" in want or defaults:
        say(f"is priced at {format_inr(prod.price)}")
        supported = True
    if "stock" in want or defaults:
        say(f"is {stock_text(prod)}" if prod.in_stock is not None else "has no stock status in the catalogue")
        supported = True
    if "rating" in want or defaults:
        if prod.rating is not None:
            count = f" from {prod.rating_count} ratings" if prod.rating_count else ""
            say(f"is rated {prod.rating} stars{count}")
            supported = True
        elif "rating" in want:
            sentences.append(f"The catalogue has no rating for the {prod.title} {cite}.")
    for key in p.detail_keys:
        values = prod.attr_values(key)
        if values:
            sentences.append(f"The {key} of the {prod.title} is {', '.join(values)} {cite}.")
            supported = True
        else:
            sentences.append(f"The catalogue does not list a {key} for the {prod.title} {cite}.")
    if "detail" in want or p.spec.desc_tokens or p.unknown or foreign:
        extra, ok = description_check(index, prod, p, foreign)
        sentences += extra
        supported = supported or ok
    return Response(" ".join(sentences), ANSWER if supported else REFUSE)


def compare(index: CatalogueIndex, pids: list[str], p: Parsed) -> Response:
    """Up to four products side by side; names the cheaper / costlier / better-rated one when asked."""
    prods = [index.products[i] for i in pids][:4]
    want = (p.intents & {"price", "stock", "rating"}) or {"price"}
    lines = ["Here is the comparison:"]
    lines += [product_line(x, "price" in want, "rating" in want, "stock" in want) for x in prods]
    sort = p.spec.sort
    if sort in ("price_asc", "price_desc"):
        pick = (min if sort == "price_asc" else max)(prods, key=lambda x: x.price)
        if any(x.price != pick.price for x in prods):
            word = "cheaper" if sort == "price_asc" else "more expensive"
            lines.append(f"The {pick.title} is the {word} one at {format_inr(pick.price)} [{pick.id}].")
        else:
            lines.append(f"They are the same price, {format_inr(pick.price)} " + " ".join(f"[{x.id}]" for x in prods) + ".")
    elif sort == "rating_desc" and (rated := [x for x in prods if x.rating is not None]):
        pick = max(rated, key=lambda x: x.rating)
        lines.append(f"The {pick.title} has the higher rating, {pick.rating} stars [{pick.id}].")
    return Response("\n".join(lines), ANSWER)


def description_check(index: CatalogueIndex, prod: Product, p: Parsed,
                      foreign: list[str] = ()) -> tuple[list[str], bool]:
    """Quote the description sentence for each asked-about word, or say the catalogue doesn't cover it."""
    out, ok = [], False
    in_description = index.desc_tokens[prod.id]
    for tok in dict.fromkeys(p.spec.desc_tokens + p.unknown + list(foreign)):
        if tok in in_description:
            sentence = next((s for s in re.split(r"(?<=[.!?])\s+", prod.description) if tok in s.lower()),
                            prod.description)
            out.append(f'The description says: "{sentence}" [{prod.id}]')
            ok = True
        else:
            out.append(f"I don't have information about \"{tok}\" for the {prod.title} [{prod.id}].")
    return out, ok
