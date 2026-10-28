"""Cited catalogue lines shared by the reply builders."""

from __future__ import annotations

from ..catalogue import CatalogueIndex, Product
from ..text import format_inr, tokenize
from .response import Claims

SORT_LABELS = {"price_asc": "lowest-priced", "price_desc": "highest-priced", "rating_desc": "highest-rated"}


def head_nouns(index: CatalogueIndex) -> frozenset:
    """Words that name what a product is: the last word of each title (\"... Running Shoes (Blue)\" -> \"shoe\")."""
    nouns = set()
    for p in index.products.values():
        words = [w.replace("-", "") for w in p.title.split("(")[0].split()]  # "T-Shirt" -> "TShirt"
        words = [w for w in words if w.isalpha()]
        if words:
            toks = tokenize(words[-1])
            nouns.update(toks[-1:])
    return frozenset(nouns)


def stock_text(p: Product) -> str:
    """Stock as shopper words; unknown stock is said to be not listed."""
    if p.in_stock is None:
        return "stock status not listed"
    return "in stock" if p.in_stock else "out of stock"


def product_line(p: Product, claims: Claims, price=True, rating=True, stock=True) -> str:
    """One cited catalogue line, e.g. '- Title: ₹1,299, rated 4.1 stars, in stock [P0001]', recorded in `claims`."""
    claims.cite(p)
    parts = []
    if price:
        parts.append(format_inr(p.price))
        claims.price(p)
    if rating and p.rating is not None:
        parts.append(f"rated {p.rating} stars")
        claims.fact(p, "rating", p.rating)
    if stock:
        parts.append(stock_text(p))
        claims.stock(p)
    return f"- {p.title}: {', '.join(parts)} [{p.id}]"
