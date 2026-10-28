"""Known gaps, fixed: follow-up questions about the products just listed, "A or B, which is better", and questions
the policy is silent on. Uses a small pack-style catalogue and policy written here."""

import json
import tempfile
import unittest
from pathlib import Path

from groundtruth.answering import ANSWER, CLARIFY, REFUSE, Session
from groundtruth.builder import build_assistant


def product(pid, title, sub, price, attrs):
    return {
        "product_id": pid, "title": title, "brand": title.split()[0], "category": "Test", "sub_category": sub,
        "description": f"{title}.", "attributes": attrs, "price_inr": price, "mrp_inr": price,
        "stock_status": "in_stock", "rating": 4.0, "review_count": 10,
    }  # fmt: skip


CATALOGUE = [
    product("SKU-RS1", "Runvex Zen Men's Running Shoes - White", "Running Shoes", 1999,
            {"gender": "men", "sizes": ["UK 7", "UK 8"], "colour": "white"}),
    product("SKU-RS2", "Kadambh Breeze Men's Running Shoes - Tan", "Running Shoes", 2499,
            {"gender": "men", "sizes": ["UK 9", "UK 10"], "colour": "tan"}),
    product("SKU-TS1", "Northline Aero Men's Cotton T-Shirt - Beige", "T-Shirts", 349, {"colour": "beige"}),
    product("SKU-SH1", "Tanvika Zen Men's Cotton Formal Shirt - Blue", "Shirts", 899, {"colour": "blue"}),
    product("SKU-EB1", "Wavefin Ripple Wireless Earbuds - Purple", "Wireless Earbuds", 339, {"colour": "purple"}),
    product("SKU-EB2", "Wavefin Ripple Wireless Earbuds - Teal", "Wireless Earbuds", 359, {"colour": "teal"}),
    product("SKU-BG1", "Yatrik Vega Duffel Bag - Brown", "Duffel Bags", 1899,
            {"luggage_size": "cabin", "colour": "brown"}),
]  # fmt: skip

POLICY = """# Policy

## 1. Returns

### 1.5 Refund timeline and method
The refund is initiated within 2 business days of pickup.

## 2. Shipping and Delivery

### 2.1 Delivery charges
Delivery is free on orders of ₹499 or more.

### 2.2 Delivery timelines
Metro cities: usually 2-4 business days after the order is placed.
"""


class GapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp:
            cat, pol = Path(tmp) / "catalogue.jsonl", Path(tmp) / "policy.md"
            cat.write_text("".join(json.dumps(p) + "\n" for p in CATALOGUE), encoding="utf-8")
            pol.write_text(POLICY, encoding="utf-8")
            cls.a = build_assistant(cat, pol)

    def ask(self, *turns):
        session = Session()
        for turn in turns:
            reply = self.a.ask(turn, session)
        return reply

    def test_which_sizes_after_a_listing_answers_from_the_listed_products(self):
        r = self.ask("men's running shoes", "which sizes")
        self.assertEqual(r.action, ANSWER)
        self.assertEqual(set(r.claims.products), {"SKU-RS1", "SKU-RS2"})
        self.assertIn(("SKU-RS1", "sizes", ["UK 7", "UK 8"]), r.claims.facts)
        self.assertNotIn("luggage_size", r.text)  # only keys the listed products have

    def test_a_choice_between_two_kinds_is_a_clarifying_question(self):
        r = self.ask("tshirt or shirt which is better")
        self.assertEqual(r.action, CLARIFY)
        self.assertTrue({"SKU-TS1", "SKU-SH1"} <= set(r.claims.products))
        self.assertEqual(r.claims.prices, [])

    def test_variants_compared_on_price_are_answered_not_treated_as_a_choice(self):
        r = self.ask("wavefin ripple purple ya teal earbuds kaunsa sasta")
        self.assertNotEqual(r.action, CLARIFY)
        self.assertIn("SKU-EB1", r.claims.products)

    def test_a_question_the_policy_is_silent_on_is_refused_with_what_it_does_say(self):
        for question in ("do you deliver on sunday", "can i get same day delivery"):
            with self.subTest(question):
                r = self.ask(question)
                self.assertEqual(r.action, REFUSE)
                self.assertIn("doesn't say anything about", r.text)
                self.assertEqual(r.claims.sections, ["2.2"])  # delivery timelines, not the refund timeline

    def test_a_question_the_policy_answers_is_still_answered(self):
        r = self.ask("free delivery kitne ke order pe")
        self.assertEqual((r.action, r.claims.sections), (ANSWER, ["2.1"]))


if __name__ == "__main__":
    unittest.main()
