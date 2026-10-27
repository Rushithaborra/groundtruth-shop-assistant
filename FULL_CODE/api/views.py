"""Shopper-facing records for the web demo: example questions, product and policy cards, the parsed reading."""
from __future__ import annotations

import re
from collections import Counter

from groundtruth import Session
from groundtruth.models.semantic import type_words
from groundtruth.text import format_inr, tokenize


def build_examples(assistant) -> list[dict]:
    """Example questions built from the loaded catalogue, each kept only if it shows what its label says.

    Built from the data rather than hard-coded, so on any catalogue (including the real one) every example works.
    """
    index, found = assistant.index, []

    def keep(label: str, question: str, want: str) -> bool:
        if any(e["q"] == question for e in found):
            return False
        if assistant.ask(question, Session()).action == want:
            found.append({"label": label, "q": question, "shows": want})
            return True
        return False

    groups = product_nouns(index)
    # a trap: "the <colour> <product>" when several products fit
    for t, pids in groups:
        colours = Counter(v for pid in pids for v in index.products[pid].attr_values("color"))
        if any(n >= 3 and keep("Trap", f"price of the {c.lower()} {t}", "clarify") for c, n in colours.most_common(3)):
            break
    # Hinglish with a budget that has matches
    for t, pids in groups:
        materials = Counter(v for pid in pids for v in index.products[pid].attr_values("material"))
        done = False
        for m, n in materials.most_common(2):
            prices = sorted(index.products[p].price for p in pids if m in index.products[p].attr_values("material"))
            if n >= 3:
                budget = int(-(-prices[len(prices) // 2] // 500) * 500)
                done = keep("Hinglish", f"{m.lower()} {t} {budget} ke andar", "answer")
            if done:
                break
        if done:
            break
    for label, q, want in [("Not sold", "do you sell iPhones?", "refuse"), ("Policy", "refund kab milega", "answer"),
                           ("Closest match", "headphones", "refuse"), ("Policy", "delivery to dubai?", "answer"),
                           ("Not sold", "red saree", "refuse")]:
        keep(label, q, want)
    # a budget that's too low: a useful refusal
    for t, pids in groups[:3]:
        cheapest = min(index.products[p].price for p in pids)
        if cheapest > 100 and keep("Too cheap", f"{t} under 100", "refuse"):
            break
    return found


def product_nouns(index) -> list[tuple[str, list[str]]]:
    """What products are called, read from the end of their titles ("... Running Shoes (Blue)" -> "running shoes").

    Two words when the first also names a product type ("power bank", "running shoes"), else one. Most common first.
    """
    kinds = set(type_words(index))
    groups: dict[str, list[str]] = {}
    for p in index.products.values():
        noun = noun_of(p.title, kinds).lower()
        if noun:
            groups.setdefault(noun, []).append(p.id)
    return sorted(((n, ids) for n, ids in groups.items() if len(ids) >= 3), key=lambda g: -len(g[1]))


def noun_of(title: str, kinds) -> str:
    """What a product is called, from the end of its title: "... Running Shoes (Blue)" -> "Running Shoes"."""
    words = [w for w in re.sub(r"\(.*?\)", " ", title).split() if w.isalpha()]
    if not words:
        return ""
    if len(words) >= 2 and tokenize(words[-2]) and tokenize(words[-2])[0] in kinds:
        return f"{words[-2]} {words[-1]}"
    return words[-1]


def product_record(p, kinds=frozenset()) -> dict:
    return {"kind": "product", "id": p.id, "title": p.title, "noun": noun_of(p.title, kinds), "price": format_inr(p.price), "category": p.category,
            "in_stock": p.in_stock, "rating": p.rating, "rating_count": p.rating_count,
            "description": p.description, "attributes": {k: p.attr_values(k) for k in p.attributes}}


def policy_record(sec) -> dict:
    return {"kind": "policy", "id": f"policy:{sec.id}", "title": sec.heading, "body": sec.body}


def reading(parsed) -> dict:
    """How the query was understood, in shopper terms (no internals)."""
    s = parsed.spec
    return {"looking_for": s.type_tokens, "features": s.desc_tokens, "filters": s.filters,
            "excluding": s.exclude, "budget_min": s.price_min, "budget_max": s.price_max, "size": s.size,
            "sort": s.sort, "not_recognised": parsed.unknown, "brands_not_carried": parsed.external,
            "read_as": [list(m) for m in parsed.mapped], "asking_about": sorted(parsed.intents)}


def remembered(session: Session) -> dict:
    s = session.spec
    return {"looking_for": s.type_tokens, "features": s.desc_tokens, "filters": s.filters, "excluding": s.exclude,
            "budget_min": s.price_min, "budget_max": s.price_max, "size": s.size, "sort": s.sort,
            "last_product": session.last_product}
