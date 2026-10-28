"""The real catalogue's format is unknown: loading must tolerate messy fields and never crash on one bad row.

Run: python -m unittest discover -s tests
"""
import json
import logging
import tempfile
import unittest
from pathlib import Path

from groundtruth.paths import ROOT
from groundtruth.answering import ShoppingAssistant
from groundtruth.catalogue import CatalogueIndex
from groundtruth.policy import load_policy
from groundtruth.catalogue.loader import _parse_stock, load_catalogue


def write(suffix: str, text: str) -> Path:
    fh = tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False, encoding="utf-8")
    fh.write(text)
    fh.close()
    return Path(fh.name)


class _Collect(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def load_quiet(path, **kw):
    """Load and return (products, warnings logged by the loader), keeping the warnings off the console."""
    logger = logging.getLogger("groundtruth.catalogue.loader")
    handler, propagate = _Collect(), logger.propagate
    logger.addHandler(handler)
    logger.propagate = False
    try:
        return load_catalogue(path, **kw), "\n".join(handler.messages)
    finally:
        logger.removeHandler(handler)
        logger.propagate = propagate


class CatalogueLoadingTests(unittest.TestCase):
    def test_price_strings(self):
        rows = [{"id": f"A{i}", "title": "Tee", "price": v} for i, v in
                enumerate(["₹1,299", "Rs. 1299.00", "1,299/-", "INR 1299", 1299, "1299"])]
        products, _ = load_quiet(write(".jsonl", "\n".join(json.dumps(r) for r in rows)))
        self.assertEqual([p.price for p in products], [1299.0] * 6)

    def test_stock_strings(self):
        cases = {"Only 2 left": True, "only 2 left in stock": True, "Limited stock": True, "In Stock ": True,
                 "OUT OF STOCK": False, "Sold out": False, "0": False, "12": True, 0: False, 3: True, True: True,
                 "pre-order": None, "": None, None: None, "ships in 3 days": True}
        for value, expected in cases.items():
            self.assertEqual(_parse_stock(value), expected, repr(value))

    def test_missing_stock_is_unknown_not_in_stock(self):
        products, _ = load_quiet(write(".jsonl", json.dumps({"id": "A1", "title": "Tee", "price": 100})))
        self.assertIsNone(products[0].in_stock)
        policy = load_policy(ROOT / "data" / "policy.txt")
        reply = ShoppingAssistant(CatalogueIndex(products), policy).ask("is A1 in stock").text
        self.assertNotIn("in stock [", reply)
        self.assertIn("no stock status", reply)

    def test_ratings_and_counts(self):
        rows = [{"id": "A1", "title": "Tee", "price": 1, "rating": "4.2/5", "rating_count": "1,234"},
                {"id": "A2", "title": "Tee", "price": 1, "avg_rating": "4.5 out of 5", "reviews": "87 reviews"}]
        products, _ = load_quiet(write(".jsonl", "\n".join(json.dumps(r) for r in rows)))
        self.assertEqual([(p.rating, p.rating_count) for p in products], [(4.2, 1234), (4.5, 87)])

    def test_csv_with_flat_attribute_columns_bom_and_header_case(self):
        text = "\ufeffProduct_ID,Name,Price,Stock_Status,Colour,Brand,Material\nX1,Blue Tee,₹499,In stock,Blue,Acme,cotton\n"
        products, _ = load_quiet(write(".csv", text))
        p = products[0]
        self.assertEqual((p.id, p.title, p.price, p.in_stock), ("X1", "Blue Tee", 499.0, True))
        self.assertEqual(p.attributes, {"color": "Blue", "brand": "Acme", "material": "cotton"})

    def test_attributes_as_json_string_and_wrapped_json(self):
        data = {"products": [{"sku": "S1", "product_name": "Mug", "selling_price": "₹250",
                              "attributes": "{\"colour\": \"red\"}"}]}
        products, _ = load_quiet(write(".json", json.dumps(data)))
        self.assertEqual((products[0].id, products[0].attributes), ("S1", {"color": "red"}))

    def test_bad_rows_are_skipped_and_reported(self):
        lines = [json.dumps({"id": "OK1", "title": "Tee", "price": 100}),
                 "{not json",
                 json.dumps({"id": "NOPRICE", "title": "Tee"}),
                 json.dumps({"id": "BADPRICE", "title": "Tee", "price": "call us"}),
                 json.dumps({"title": "No id", "price": 5}),
                 json.dumps({"id": "OK1", "title": "Duplicate", "price": 7}),
                 json.dumps({"id": "OK2", "title": "Tee", "price": 200})]
        products, err = load_quiet(write(".jsonl", "\n".join(lines)))
        self.assertEqual([p.id for p in products], ["OK1", "OK2"])
        self.assertIn("skipped 5", err)
        with self.assertRaises(ValueError):
            load_quiet(write(".jsonl", "\n".join(lines)), strict=True)


if __name__ == "__main__":
    unittest.main()
