"""What the assistant returns for one message, and the per-shopper conversation state."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from ..catalogue import Product, Spec

ANSWER, CLARIFY, REFUSE = "answer", "clarify", "refuse"
CITATION = re.compile(r"\[([^\]]+)\]")


@dataclass
class Claims:
    """What a reply states, recorded by the code that writes it: cited products and policy sections, prices, and
    other facts under the catalogue's own field names. Each value is copied from the catalogue record, never parsed
    back out of the reply text; stating a price or fact about a product also cites it."""

    products: list[str] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)
    prices: list[tuple[str, float]] = field(default_factory=list)
    facts: list[tuple[str, str, Any]] = field(default_factory=list)  # (product id, catalogue field, value)

    def cite(self, product: Product) -> None:
        """Cite a product (once)."""
        if product.id not in self.products:
            self.products.append(product.id)

    def price(self, product: Product) -> None:
        """State a product's selling price, from its catalogue record."""
        self.cite(product)
        if (product.id, product.price) not in self.prices:
            self.prices.append((product.id, product.price))

    def fact(self, product: Product, catalogue_field: str, value: Any) -> None:
        """State a fact about a product under a catalogue field name."""
        self.cite(product)
        if (product.id, catalogue_field, value) not in self.facts:
            self.facts.append((product.id, catalogue_field, value))

    def stock(self, product: Product) -> None:
        """State the catalogue's own stock value, if it gives one."""
        if product.stock_status is not None:
            self.fact(product, "stock_status", product.stock_status)

    def rating(self, product: Product) -> None:
        """State the rating and review count, if the catalogue has a rating."""
        if product.rating is not None:
            self.fact(product, "rating", product.rating)
            if product.rating_count:
                self.fact(product, "review_count", product.rating_count)

    def attribute(self, product: Product, key: str) -> None:
        """An attribute stated in the reply, reported under the catalogue's own key with its raw value."""
        if key in product.attributes:
            self.fact(product, product.source_key(key), product.attributes[key])

    def section(self, section_id: str) -> None:
        """Cite a policy section (once)."""
        if section_id not in self.sections:
            self.sections.append(section_id)

    def merge(self, other: Claims) -> None:
        """Add another reply's claims (for a reply made of two parts)."""
        for pid in other.products:
            if pid not in self.products:
                self.products.append(pid)
        for sid in other.sections:
            self.section(sid)
        self.prices += [x for x in other.prices if x not in self.prices]
        self.facts += [x for x in other.facts if x not in self.facts]


@dataclass
class Response:
    """One reply: its text with [ID] citations, the action (answer, clarify or refuse), the claims it makes, and how it
    was produced (path, time, tokens)."""

    text: str
    action: str
    latency_ms: float = 0.0
    context_tokens: int = 0
    output_tokens: int = 0
    path: str = "rules"  # rules | semantic | llm: which understanding path resolved the query
    llm_input_tokens: int = 0  # real tokens sent to / produced by the instruct model (0 on other paths)
    llm_output_tokens: int = 0
    claims: Claims = field(default_factory=Claims)

    @property
    def citations(self) -> list[str]:
        """The citations written in the text, in order, without repeats."""
        return list(dict.fromkeys(CITATION.findall(self.text)))


@dataclass
class Session:
    """What a chat remembers between messages: the current search (filters, budget), the last product talked about
    and the last listing."""

    spec: Spec = field(default_factory=Spec)
    last_product: str | None = None
    shown: list[str] = field(default_factory=list)  # products in the last listing, for "which sizes?"
    budget_kept: bool = False  # `spec` found nothing only because of its budget, kept for the next turn
    # Web chat only (off for scoring): moving to another product carries the budget, size and brand/colour/gender over,
    # earlier searches are kept in `history`, and "back to the jeans" returns to one.
    carry: bool = False
    history: list[dict] = field(default_factory=list)  # earlier searches: {"label", "kinds", "spec"}; newest last
    carry_from: Spec | None = None  # this turn only: the search being left, to carry things from

    def clear(self) -> None:
        """Forget everything the session remembers."""
        self.spec, self.last_product, self.shown, self.budget_kept = Spec(), None, [], False
        self.history, self.carry_from = [], None


def spec_to_dict(spec: Spec) -> dict:
    """A search spec as plain JSON data."""
    return asdict(spec)


def spec_from_dict(data: dict) -> Spec:
    """A search spec from `spec_to_dict`'s data (measurements go back to (unit, number) pairs)."""
    fields = dict(data)
    fields["measures"] = [tuple(m) for m in fields.get("measures", [])]
    return Spec(**fields)
