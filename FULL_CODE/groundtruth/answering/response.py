"""What the assistant returns for one message, and the per-shopper conversation state."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
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
        if product.id not in self.products:
            self.products.append(product.id)

    def price(self, product: Product) -> None:
        self.cite(product)
        if (product.id, product.price) not in self.prices:
            self.prices.append((product.id, product.price))

    def fact(self, product: Product, catalogue_field: str, value: Any) -> None:
        self.cite(product)
        if (product.id, catalogue_field, value) not in self.facts:
            self.facts.append((product.id, catalogue_field, value))

    def stock(self, product: Product) -> None:
        if product.stock_status is not None:
            self.fact(product, "stock_status", product.stock_status)

    def rating(self, product: Product) -> None:
        if product.rating is not None:
            self.fact(product, "rating", product.rating)
            if product.rating_count:
                self.fact(product, "review_count", product.rating_count)

    def attribute(self, product: Product, key: str) -> None:
        """An attribute stated in the reply, reported under the catalogue's own key with its raw value."""
        if key in product.attributes:
            self.fact(product, product.source_key(key), product.attributes[key])

    def section(self, section_id: str) -> None:
        if section_id not in self.sections:
            self.sections.append(section_id)

    def merge(self, other: Claims) -> None:
        for pid in other.products:
            if pid not in self.products:
                self.products.append(pid)
        for sid in other.sections:
            self.section(sid)
        self.prices += [x for x in other.prices if x not in self.prices]
        self.facts += [x for x in other.facts if x not in self.facts]


@dataclass
class Response:
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
        return list(dict.fromkeys(CITATION.findall(self.text)))


@dataclass
class Session:
    spec: Spec = field(default_factory=Spec)
    last_product: str | None = None
    shown: list[str] = field(default_factory=list)  # products in the last listing, for "which sizes?"

    def clear(self) -> None:
        self.spec, self.last_product, self.shown = Spec(), None, []
