"""Catalogue loading. Accepts .jsonl / .json / .csv with a few common column spellings.

The real catalogue's exact format is unknown, so parsing is tolerant: prices like "₹1,299" or "Rs. 1299.00", stock
like "Only 2 left", ratings like "4.2/5", attributes as a dict, a JSON string, or flat columns. A row that can't be
parsed (no id, title or price) is skipped and reported on stderr instead of stopping the whole load. Values the
catalogue doesn't give are left unknown (None), never filled with a guess.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Product:
    id: str
    title: str
    description: str
    category: str
    price: float
    in_stock: bool | None  # None: the catalogue doesn't say
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


KEY_ALIASES = {"colour": "color", "colours": "color", "colors": "color", "fabric": "material", "sizes": "size",
               "manufacturer": "brand", "make": "brand"}


def _clean_attributes(attrs: dict) -> dict:
    """Lower-case keys, fold spelling variants onto one key, strip whitespace in values."""
    clean: dict = {}
    for key, value in attrs.items():
        key = KEY_ALIASES.get(str(key).strip().lower(), str(key).strip().lower())
        if isinstance(value, str):
            value = value.strip()
        elif isinstance(value, (list, tuple)):
            value = [v.strip() if isinstance(v, str) else v for v in value]
        if value in ("", None, []):
            continue
        clean.setdefault(key, value)
    return clean


_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_OUT = ("out of stock", "out_of_stock", "outofstock", "oos", "unavailable", "sold out", "soldout",
        "not available", "discontinued", "currently unavailable")
_IN = ("in stock", "in_stock", "instock", "available", "left", "limited", "few", "hurry", "ships")


def _parse_stock(value) -> bool | None:
    """True / False when the catalogue says, None when it doesn't (\"pre-order\", blank, unrecognised)."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    text = " ".join(str(value).strip().lower().replace("-", " ").split())
    if text in {"no", "false", "n", "0"} or any(w in text for w in _OUT):
        return False
    if text in {"yes", "true", "y"}:
        return True
    m = _NUMBER.search(text)
    if m and not any(w in text for w in ("day", "week")):  # "Only 2 left", "12 units"; not "ships in 3 days"
        return float(m.group().replace(",", "")) > 0
    if any(w in text for w in _IN):
        return True
    return None


def _parse_number(value, kind: str) -> float | None:
    """Numbers written as \"₹1,299\", \"Rs. 1299.00\", \"1,299/-\", \"4.2/5\", \"4.2 out of 5\"."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"{kind} is a boolean")
    if isinstance(value, (int, float)):
        return float(value)
    m = _NUMBER.search(str(value))
    if not m:
        raise ValueError(f"no number in {kind} {value!r}")
    return float(m.group().replace(",", ""))


ID_KEYS = ("id", "product_id", "productid", "sku", "item_id")
TITLE_KEYS = ("title", "name", "product_name", "product_title")
DESC_KEYS = ("description", "desc", "details", "product_description")
CATEGORY_KEYS = ("category", "categories", "product_category", "department")
PRICE_KEYS = ("price", "selling_price", "sale_price", "price_inr", "mrp")  # selling price before MRP
STOCK_KEYS = ("in_stock", "stock_status", "stock", "availability", "available", "inventory", "quantity", "qty")
RATING_KEYS = ("rating", "ratings", "avg_rating", "average_rating", "stars")
COUNT_KEYS = ("rating_count", "num_ratings", "ratings_count", "review_count", "reviews", "num_reviews")
ATTR_KEYS = ("attributes", "attrs", "specs", "specifications")
CORE = {k for keys in (ID_KEYS, TITLE_KEYS, DESC_KEYS, CATEGORY_KEYS, PRICE_KEYS, STOCK_KEYS, RATING_KEYS, COUNT_KEYS,
                       ATTR_KEYS) for k in keys} | {"mrp", "image", "image_url", "url", "currency", "discount"}


def normalise_product(raw: dict) -> Product:
    raw = {str(k).strip().lower().lstrip("\ufeff"): v for k, v in raw.items()}  # CSV headers: case, spaces, BOM
    pid, title, price = _first(raw, *ID_KEYS), _first(raw, *TITLE_KEYS), _parse_number(_first(raw, *PRICE_KEYS), "price")
    if pid is None or not str(title or "").strip() or price is None:
        raise ValueError("missing id, title or price")
    rating = _parse_number(_first(raw, *RATING_KEYS), "rating")
    count = _parse_number(_first(raw, *COUNT_KEYS), "rating count")
    attributes = _parse_attributes(_first(raw, *ATTR_KEYS, default={}))
    for key, value in raw.items():  # flat attribute columns ("color", "brand", ...) beside the core fields
        if key not in CORE and key not in attributes and isinstance(value, (str, int, float, list)) and value != "":
            attributes[key] = value
    return Product(
        id=str(pid).strip(),
        title=" ".join(str(title).split()),
        description=str(_first(raw, *DESC_KEYS, default="")),
        category=str(_first(raw, *CATEGORY_KEYS, default="")),
        price=price,
        in_stock=_parse_stock(_first(raw, *STOCK_KEYS)),
        rating=rating,
        rating_count=int(count) if count is not None else None,
        attributes=_clean_attributes(attributes),
    )


def _read_rows(path: Path) -> list:
    if path.suffix == ".jsonl":
        rows = []
        for n, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    rows.append(ValueError(f"line {n}: invalid JSON ({exc.msg})"))
        return rows
    if path.suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict):  # {"products": [...]} or {"items": [...]}
            data = next((v for v in data.values() if isinstance(v, list)), [])
        return data
    if path.suffix in (".csv", ".tsv"):
        with path.open(encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh, delimiter="\t" if path.suffix == ".tsv" else ","))
    raise ValueError(f"Unsupported catalogue format: {path.suffix}")


def load_catalogue(path: str | Path, strict: bool = False) -> list[Product]:
    """Load products; bad rows are skipped and reported on stderr (strict=True raises instead)."""
    products, seen, skipped = [], set(), []
    for n, row in enumerate(_read_rows(Path(path)), 1):
        try:
            if isinstance(row, Exception):
                raise row
            if not isinstance(row, dict):
                raise ValueError("row is not an object")
            product = normalise_product(row)
            if product.id in seen:
                raise ValueError(f"duplicate id {product.id}")
        except (ValueError, TypeError) as exc:
            if strict:
                raise ValueError(f"catalogue row {n}: {exc}") from exc
            skipped.append(f"row {n}: {exc}")
            continue
        seen.add(product.id)
        products.append(product)
    if skipped:
        print(f"[catalogue] skipped {len(skipped)} unreadable row(s): " + "; ".join(skipped[:5])
              + (" ..." if len(skipped) > 5 else ""), file=sys.stderr)
    return products
