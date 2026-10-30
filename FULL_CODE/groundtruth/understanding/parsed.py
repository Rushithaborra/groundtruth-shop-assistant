"""The parser's reading of one shopper message."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..catalogue import Spec


@dataclass
class Parsed:
    """What the parser read from one message: intents, a product id if one was named, the search spec, and the words
    it couldn't place."""

    raw: str
    intents: set[str] = field(default_factory=set)
    product_id: str | None = None
    spec: Spec = field(default_factory=Spec)
    unknown: list[str] = field(default_factory=list)
    detail_keys: list[str] = field(default_factory=list)
    mentioned: list[str] = field(default_factory=list)  # products whose full title is in the query
    keywords: list[str] = field(default_factory=list)  # tokens left after stopword removal (policy matching)
    external: list[str] = field(default_factory=list)  # well-known brands/products the catalogue doesn't contain
    alternatives: list[str] = field(default_factory=list)  # catalogue type words related to an unknown product word
    mapped: list[tuple[str, str]] = field(default_factory=list)  # (shopper word, catalogue word) from the fallback
    corrected: list[tuple[str, str]] = field(default_factory=list)  # (typed, shown) short words mended by one edit
    suggestions: list[tuple[str, str]] = field(default_factory=list)  # (typed, product word) "did you mean" offers
    refined: bool = False  # set by the assistant when this turn was merged into the previous search
    superlative: bool = False  # one product wanted ("sabse sasta"), not a list
    is_question: bool = False  # asked as a question ("?", "kya", "hai"), not a request to browse
    choice: list[str] = field(default_factory=list)  # two product kinds to choose between ("tshirt or shirt")
    all_tokens: set[str] = field(default_factory=set)  # every token (exact title matching)

    @property
    def has_product_ref(self) -> bool:
        """The message names a product, a kind of product, a filter or a size."""
        s = self.spec
        return bool(self.product_id or self.mentioned or s.type_tokens or s.desc_tokens or s.filters or s.size)

    @property
    def has_constraint(self) -> bool:
        """Any product reference, budget, measurement or exclusion."""
        s = self.spec
        return (
            self.has_product_ref
            or s.price_min is not None
            or s.price_max is not None
            or bool(s.exclude)
            or bool(s.measures)
        )
