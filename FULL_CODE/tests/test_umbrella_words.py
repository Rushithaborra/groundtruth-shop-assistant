"""Shoppers say "clothes", "clothing" or "kapde" for what the catalogue calls Apparel. These must list apparel (within
any budget) instead of refusing with "I don't have anything matching 'clothe'". A small pack-style catalogue."""

import json
import tempfile
import unittest
from pathlib import Path

from groundtruth.answering import ANSWER, REFUSE, Session
from groundtruth.builder import build_assistant

POLICY = "# Policy\n\n## 1. Returns\n\n### 1.1 Return window\nReturns within 30 days.\n"


def product(pid, title, category, sub, price):
    return {
        "product_id": pid,
        "title": title,
        "brand": title.split()[0],
        "category": category,
        "sub_category": sub,
        "description": f"{title}.",
        "attributes": {},
        "price_inr": price,
        "mrp_inr": price,
        "stock_status": "in_stock",
        "rating": 4.0,
        "review_count": 10,
    }


CATALOGUE = [
    product("SKU-A1", "Threadly Kora Men's Cotton Shirt - Blue", "Apparel", "Shirts", 399),
    product("SKU-A2", "Threadly Sera Women's Cotton Kurta - Red", "Apparel", "Kurtas", 899),
    product("SKU-A3", "Lomax Vida Unisex Cotton T-Shirt - Black", "Apparel", "T-Shirts", 1999),
    product("SKU-F1", "Stepzo Glide Men's Sandals - Black", "Footwear", "Sandals", 450),
]
APPAREL = {"SKU-A1", "SKU-A2", "SKU-A3"}


class UmbrellaWordTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp:
            cat, pol = Path(tmp) / "catalogue.jsonl", Path(tmp) / "policy.md"
            cat.write_text("".join(json.dumps(p) + "\n" for p in CATALOGUE), encoding="utf-8")
            pol.write_text(POLICY, encoding="utf-8")
            cls.a = build_assistant(cat, pol)

    def test_clothes_words_list_apparel_not_a_refusal(self):
        for word in ("clothes", "clothing", "garments", "outfits", "kapde", "kapda", "battalu"):
            r = self.a.ask(f"{word} under 1000", Session())
            self.assertEqual(r.action, ANSWER, word)
            self.assertTrue(r.claims.products, word)
            self.assertLessEqual(set(r.claims.products), APPAREL, f"{word} listed something that is not apparel")

    def test_the_budget_is_kept(self):
        r = self.a.ask("clothes under 500", Session())
        self.assertEqual(set(r.claims.products), {"SKU-A1"})  # the sandals cost 450 but are not clothes

    def test_the_word_order_from_the_demo_works_after_a_product_answer(self):
        s = Session()
        self.a.ask("price of Threadly Kora Men's Cotton Shirt - Blue", s)
        r = self.a.ask("5000 under clothes", s)  # was "I don't have information about 'clothe' for the shirt"
        self.assertEqual(r.action, ANSWER)
        self.assertLessEqual(set(r.claims.products), APPAREL)

    def test_an_unknown_word_still_refuses(self):
        self.assertEqual(self.a.ask("gizmos under 1500", Session()).action, REFUSE)


if __name__ == "__main__":
    unittest.main()
