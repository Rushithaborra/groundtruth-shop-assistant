"""Catalogue loading. Accepts .jsonl / .json / .csv with a few common column spellings."""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Product:
    id: str
    title: str
    description: str
    category: str
    price: float
    in_stock: bool
    rating: float | None = None
    rating_count: int | None = None
    attributes: dict = field(default_factory=dict)

    def attr_values(self, key: str) -> list[str]:
        value = self.attributes.get(key)
        if value is None:
            return []
        if isinstance(value, (list, tuple)):
            return [str(v) for v in value]
        return [str(value)]


def _first(raw: dict, *keys, default=None):
    for key in keys:
        if key in raw and raw[key] not in (None, ""):
            return raw[key]
    return default


def _parse_attributes(value) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    text = str(value).strip()
    if text.startswith("{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    attrs = {}
    for chunk in text.replace("|", ";").split(";"):
        if ":" in chunk:
            key, _, val = chunk.partition(":")
            attrs[key.strip().lower()] = val.strip()
    return attrs


def _parse_stock(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    text = str(value).strip().lower()
    if text in {"out of stock", "out_of_stock", "oos", "no", "false", "0", "unavailable", "sold out"}:
        return False
    return text in {"in stock", "in_stock", "available", "yes", "true", "1"} or text.isdigit() and int(text) > 0


def normalise_product(raw: dict) -> Product:
    stock = _first(raw, "in_stock", "stock_status", "stock", "availability", default=True)
    rating = _first(raw, "rating", "ratings", "avg_rating")
    rating_count = _first(raw, "rating_count", "num_ratings", "reviews", "review_count")
    return Product(
        id=str(_first(raw, "id", "product_id", "sku")),
        title=str(_first(raw, "title", "name", default="")),
        description=str(_first(raw, "description", "desc", default="")),
        category=str(_first(raw, "category", "categories", default="")),
        price=float(str(_first(raw, "price", "mrp", default=0)).replace(",", "")),
        in_stock=_parse_stock(stock),
        rating=float(rating) if rating is not None else None,
        rating_count=int(float(rating_count)) if rating_count is not None else None,
        attributes=_parse_attributes(_first(raw, "attributes", "attrs", default={})),
    )


def load_catalogue(path: str | Path) -> list[Product]:
    path = Path(path)
    if path.suffix == ".jsonl":
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    elif path.suffix == ".json":
        rows = json.loads(path.read_text(encoding="utf-8"))
    elif path.suffix == ".csv":
        with path.open(encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
    else:
        raise ValueError(f"Unsupported catalogue format: {path.suffix}")
    return [normalise_product(r) for r in rows]
