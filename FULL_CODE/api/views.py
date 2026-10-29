"""Shopper-facing records for the web demo: example questions, product and policy cards, the parsed reading."""

from __future__ import annotations

import re
from collections import Counter

from groundtruth.answering import Session, ShoppingAssistant
from groundtruth.catalogue import CatalogueIndex, Product
from groundtruth.models.semantic import type_words
from groundtruth.policy import Section
from groundtruth.text import format_inr, tokenize
from groundtruth.understanding import Parsed

# questions that don't depend on the catalogue's products: what each should show, and the label on its chip
FIXED_EXAMPLES = (
    ("Not sold", "do you sell iPhones?", "refuse"),
    ("Policy", "refund kab milega", "answer"),
    ("Closest match", "headphones", "refuse"),
    ("Policy", "delivery to dubai?", "answer"),
    ("Not sold", "red saree", "refuse"),
)


class _Examples:
    """Collects example questions, keeping each one only if the reply is the kind its label promises."""

    def __init__(self, assistant: ShoppingAssistant) -> None:
        self.assistant, self.index = assistant, assistant.index
        self.found: list[dict] = []

    def keep(self, label: str, question: str, want: str) -> bool:
        """Keep an example question if it is new and the assistant really answers it with the expected action."""
        if any(e["q"] == question for e in self.found):
            return False
        if self.assistant.ask(question, Session()).action == want:
            self.found.append({"label": label, "q": question, "shows": want})
            return True
        return False

    def trap(self, groups: list[tuple[str, list[str]]]) -> None:
        """A trap: "the <colour> <product>" when several products fit."""
        for t, pids in groups:
            colours = Counter(v for pid in pids for v in self.index.products[pid].attr_values("color"))
            top = colours.most_common(3)
            if any(n >= 3 and self.keep("Trap", f"price of the {c.lower()} {t}", "clarify") for c, n in top):
                break

    def hinglish_budget(self, groups: list[tuple[str, list[str]]]) -> None:
        """Hinglish with a budget that has matches ("cotton kurta 1500 ke andar")."""
        for t, pids in groups:
            if self._budget_question(t, pids):
                break

    def _budget_question(self, noun: str, pids: list[str]) -> bool:
        """Add a Hinglish budget example ("cotton kurta 1500 ke andar") for a common material."""
        products = self.index.products
        materials = Counter(v for pid in pids for v in products[pid].attr_values("material"))
        for material, n in materials.most_common(2):
            prices = sorted(products[p].price for p in pids if material in products[p].attr_values("material"))
            if n >= 3:
                budget = int(-(-prices[len(prices) // 2] // 500) * 500)
                if self.keep("Hinglish", f"{material.lower()} {noun} {budget} ke andar", "answer"):
                    return True
        return False

    def too_cheap(self, groups: list[tuple[str, list[str]]]) -> None:
        """A budget that's too low: a useful refusal."""
        for t, pids in groups[:3]:
            cheapest = min(self.index.products[p].price for p in pids)
            if cheapest > 100 and self.keep("Too cheap", f"{t} under 100", "refuse"):
                break


def build_examples(assistant: ShoppingAssistant) -> list[dict]:
    """Example questions built from the loaded catalogue, each kept only if it shows what its label says.

    Built from the data rather than hard-coded, so on any catalogue (including the real one) every example works.
    """
    examples, groups = _Examples(assistant), product_nouns(assistant.index)
    examples.trap(groups)
    examples.hinglish_budget(groups)
    for label, question, want in FIXED_EXAMPLES:
        examples.keep(label, question, want)
    examples.too_cheap(groups)
    return examples.found


def product_nouns(index: CatalogueIndex) -> list[tuple[str, list[str]]]:
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


def noun_of(title: str, kinds: frozenset[str] | set[str]) -> str:
    """What a product is called, from the end of its title: "... Running Shoes (Blue)" -> "Running Shoes"."""
    words = [w for w in re.sub(r"\(.*?\)", " ", title).split() if w.isalpha()]
    if not words:
        return ""
    if len(words) >= 2 and tokenize(words[-2]) and tokenize(words[-2])[0] in kinds:
        return f"{words[-2]} {words[-1]}"
    return words[-1]


def product_record(p: Product, kinds: frozenset[str] = frozenset()) -> dict:
    """A product's full catalogue record, for its card and receipt."""
    return {
        "kind": "product",
        "id": p.id,
        "title": p.title,
        "noun": noun_of(p.title, kinds),
        "price": format_inr(p.price),
        "category": p.category,
        "in_stock": p.in_stock,
        "rating": p.rating,
        "rating_count": p.rating_count,
        "description": p.description,
        "attributes": {k: p.attr_values(k) for k in p.attributes},
    }


def policy_record(sec: Section) -> dict:
    """A policy section's text, for its receipt."""
    return {"kind": "policy", "id": f"policy:{sec.id}", "title": sec.heading, "body": sec.body}


def reading(parsed: Parsed) -> dict:
    """How the query was understood, in shopper terms (no internals)."""
    s = parsed.spec
    return {
        "looking_for": s.type_tokens,
        "features": s.desc_tokens,
        "filters": s.filters,
        "excluding": s.exclude,
        "budget_min": s.price_min,
        "budget_max": s.price_max,
        "size": s.size,
        "sort": s.sort,
        "not_recognised": parsed.unknown,
        "brands_not_carried": parsed.external,
        "read_as": [list(m) for m in parsed.mapped],
        "asking_about": sorted(parsed.intents),
    }


def remembered(session: Session) -> dict:
    """What the session carries into the next message: filters, budget, size, last product."""
    s = session.spec
    return {
        "looking_for": s.type_tokens,
        "features": s.desc_tokens,
        "filters": s.filters,
        "excluding": s.exclude,
        "budget_min": s.price_min,
        "budget_max": s.price_max,
        "size": s.size,
        "sort": s.sort,
        "last_product": session.last_product,
    }
