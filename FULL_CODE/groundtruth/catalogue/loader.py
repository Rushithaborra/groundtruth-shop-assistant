"""Catalogue loading. Accepts .jsonl / .json / .csv with a few common column spellings.

The real catalogue's exact format is unknown, so parsing is tolerant: prices like "₹1,299" or "Rs. 1299.00", stock
like "Only 2 left", ratings like "4.2/5", attributes as a dict, a JSON string, or flat columns. A row that can't be
parsed (no id, title or price) is skipped and logged as a warning instead of stopping the whole load. Values the
catalogue doesn't give are left unknown (None), never filled with a guess.
"""

from __future__ import annotations

import csv
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..errors import DataFileError

logger = logging.getLogger(__name__)


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
    attributes: dict = field(default_factory=dict)  # keys folded onto one spelling ("colour" -> "color")
    mrp: float | None = None
    stock_status: str | None = None  # the catalogue's own stock value, e.g. "low_stock"
    source_keys: dict = field(default_factory=dict)  # folded attribute key -> the catalogue's own key name

    def source_key(self, key: str) -> str:
        """The catalogue's own name for an attribute key, so stated facts use the exact field name."""
        return self.source_keys.get(key, key)

    def attr_values(self, key: str) -> list[str]:
        """An attribute's values as strings (a list attribute gives several, a missing one none)."""
        value = self.attributes.get(key)
        if value is None:
            return []
        if isinstance(value, (list, tuple)):
            return [str(v) for v in value]
        return [str(value)]


def _first(raw: dict, *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in raw and raw[key] not in (None, ""):
            return raw[key]
    return default


def _json_object(text: str) -> dict | None:
    """The text as a JSON object, or None if it isn't one."""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _parse_attributes(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    text = str(value).strip()
    if text.startswith("{"):
        parsed = _json_object(text)
        if parsed is not None:
            return parsed
    # not JSON, or malformed JSON: read "key: value; key: value" pairs instead
    attrs = {}
    for chunk in text.replace("|", ";").split(";"):
        if ":" in chunk:
            key, _, val = chunk.partition(":")
            attrs[key.strip().lower()] = val.strip()
    return attrs


KEY_ALIASES = {
    "colour": "color",
    "colours": "color",
    "colors": "color",
    "fabric": "material",
    "sizes": "size",
    "manufacturer": "brand",
    "make": "brand",
}


def _clean_attributes(attrs: dict) -> tuple[dict, dict]:
    """Lower-case keys, fold spelling variants onto one key, strip whitespace in values.

    Returns (attributes, folded key -> original key).
    """
    clean: dict = {}
    source: dict = {}
    for original, value in attrs.items():
        key = KEY_ALIASES.get(str(original).strip().lower(), str(original).strip().lower())
        if isinstance(value, str):
            value = value.strip()
        elif isinstance(value, (list, tuple)):
            value = [v.strip() if isinstance(v, str) else v for v in value]
        if value in ("", None, []) or key in clean:
            continue
        clean[key] = value
        source[key] = str(original).strip()
    return clean, source


_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")
_OUT = (
    "out of stock",
    "out_of_stock",
    "outofstock",
    "oos",
    "unavailable",
    "sold out",
    "soldout",
    "not available",
    "discontinued",
    "currently unavailable",
)
_IN_STATES = ("in stock", "in_stock", "instock", "low stock", "low_stock")  # say outright that it is in stock
_IN_HINTS = ("available", "left", "limited", "few", "hurry")  # weaker wording that is still about stock
_QUANTITY = re.compile(r"(\d[\d,]*)\s*(?:units?|pcs|pieces|items?|left|in stock|available)\b|\bonly\s+(\d[\d,]*)")
# shipping and delivery times say how long it takes, not whether it is in stock: "ships in 3 days", "ships tomorrow"
_LEAD_TIME = re.compile(
    r"\b(?:days?|weeks?|hours?|hrs?|months?|today|tomorrow|ships?|shipping|dispatch\w*|deliver\w*)\b"
)


def _parse_stock(value: object) -> bool | None:
    """True / False when the catalogue says, None when it doesn't (\"pre-order\", \"ships in 3 days\", blank)."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value > 0
    text = " ".join(str(value).strip().lower().replace("-", " ").split())
    if text in {"no", "false", "n", "0"} or any(w in text for w in _OUT):
        return False
    if text in {"yes", "true", "y"} or any(w in text for w in _IN_STATES):
        return True
    quantity = _QUANTITY.search(text)  # "Only 2 left", "12 units": a count of items, not a time
    if quantity:
        return float((quantity.group(1) or quantity.group(2)).replace(",", "")) > 0
    if _LEAD_TIME.search(text):
        return None
    if _NUMBER.fullmatch(text):
        return float(text.replace(",", "")) > 0
    return True if any(w in text for w in _IN_HINTS) else None


def _parse_number(value: object, kind: str) -> float | None:
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
MRP_KEYS = ("mrp_inr", "mrp")
STOCK_KEYS = ("in_stock", "stock_status", "stock", "availability", "available", "inventory", "quantity", "qty")
RATING_KEYS = ("rating", "ratings", "avg_rating", "average_rating", "stars")
COUNT_KEYS = ("rating_count", "num_ratings", "ratings_count", "review_count", "reviews", "num_reviews")
ATTR_KEYS = ("attributes", "attrs", "specs", "specifications")
CORE = {
    k
    for keys in (
        ID_KEYS,
        TITLE_KEYS,
        DESC_KEYS,
        CATEGORY_KEYS,
        PRICE_KEYS,
        MRP_KEYS,
        STOCK_KEYS,
        RATING_KEYS,
        COUNT_KEYS,
        ATTR_KEYS,
    )
    for k in keys
} | {"mrp", "image", "image_url", "url", "currency", "discount"}


def normalise_product(raw: dict) -> Product:
    """One catalogue row as a Product; raises ValueError if it has no id, title or price."""
    raw = {str(k).strip().lower().lstrip("\ufeff"): v for k, v in raw.items()}  # CSV headers: case, spaces, BOM
    pid, title, price = (
        _first(raw, *ID_KEYS),
        _first(raw, *TITLE_KEYS),
        _parse_number(_first(raw, *PRICE_KEYS), "price"),
    )
    if pid is None or not str(title or "").strip() or price is None:
        raise ValueError("missing id, title or price")
    rating = _parse_number(_first(raw, *RATING_KEYS), "rating")
    count = _parse_number(_first(raw, *COUNT_KEYS), "rating count")
    attributes = _parse_attributes(_first(raw, *ATTR_KEYS, default={}))
    for key, value in raw.items():  # flat attribute columns ("color", "brand", ...) beside the core fields
        if key not in CORE and key not in attributes and isinstance(value, (str, int, float, list)) and value != "":
            attributes[key] = value
    attributes, source_keys = _clean_attributes(attributes)
    stock = _first(raw, *STOCK_KEYS)
    return Product(
        id=str(pid).strip(),
        title=" ".join(str(title).split()),
        description=str(_first(raw, *DESC_KEYS, default="")),
        category=str(_first(raw, *CATEGORY_KEYS, default="")),
        price=price,
        in_stock=_parse_stock(stock),
        rating=rating,
        rating_count=int(count) if count is not None else None,
        attributes=attributes,
        mrp=_parse_number(_first(raw, *MRP_KEYS), "MRP"),
        stock_status=str(stock).strip() if isinstance(stock, str) and stock.strip() else None,
        source_keys=source_keys,
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
    raise DataFileError(f"unsupported catalogue format {path.suffix!r}: use .jsonl, .json, .csv or .tsv")


def load_catalogue(path: str | Path, strict: bool = False) -> list[Product]:
    """Load products; bad rows are skipped and logged (strict=True raises instead)."""
    path = Path(path)
    if not path.is_file():
        raise DataFileError(f"catalogue file not found: {path}")
    products, seen, skipped = [], set(), []
    for n, row in enumerate(_read_rows(path), 1):
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
        logger.warning(
            "skipped %d unreadable catalogue row(s): %s%s",
            len(skipped),
            "; ".join(skipped[:5]),
            " ..." if len(skipped) > 5 else "",
        )
    return products
