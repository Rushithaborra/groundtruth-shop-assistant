"""Replies name catalogue fields the way a shopper reads them ("water resistant: Yes", "thickness (mm)"), never as
raw field names ("water_resistant ... is True"), and the grounding checker still passes them. A small pack-style
catalogue written here."""

import json
import re
import tempfile
import unittest
from pathlib import Path

from groundtruth.answering import ANSWER, CLARIFY, Session
from groundtruth.builder import build_assistant
from groundtruth.grounding import Grounding
from groundtruth.text import field_label, value_label

RAW = re.compile(r"\b[a-z]+_[a-z_]+\b|\bTrue\b|\bFalse\b")


def product(pid, title, sub, price, attrs):
    return {
        "product_id": pid,
        "title": title,
        "brand": title.split()[0],
        "category": "Test",
        "sub_category": sub,
        "description": f"{title}.",
        "attributes": attrs,
        "price_inr": price,
        "mrp_inr": price,
        "stock_status": "in_stock",
        "rating": 4.0,
        "review_count": 10,
    }


CATALOGUE = [
    product("SKU-SP", "Wavefin Tide Bluetooth Speaker - Green", "Bluetooth Speakers", 2199, {"water_resistant": True}),
    product(
        "SKU-SP2",
        "Wavefin Surge Bluetooth Speaker - Blue",
        "Bluetooth Speakers",
        2999,
        {"water_resistant": True, "water_resistance_rating": "IPX7"},
    ),
    product("SKU-M6", "Yogix Flow Yoga Mat - Purple", "Yoga Mats", 699, {"thickness_mm": 6, "anti_slip": True}),
    product("SKU-M8", "Yogix Flow Yoga Mat - Purple", "Yoga Mats", 899, {"thickness_mm": 8, "anti_slip": False}),
]


class ReadableFieldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp:
            cat, pol = Path(tmp) / "catalogue.jsonl", Path(tmp) / "policy.md"
            cat.write_text("".join(json.dumps(p) + "\n" for p in CATALOGUE), encoding="utf-8")
            pol.write_text("# Policy\n\n## 1. Returns\n\n### 1.1 Return window\nReturns within 30 days.\n")
            cls.a = build_assistant(cat, pol)
        cls.grounding = Grounding(cls.a.index, cls.a.sections)

    def ask(self, question):
        reply = self.a.ask(question, Session())
        self.assertIsNone(RAW.search(reply.text), reply.text)
        self.assertEqual(self.grounding.check(reply.text, question)["problems"], [], reply.text)
        return reply

    def test_labels(self):
        self.assertEqual(field_label("water_resistance_rating"), "water resistance rating")
        self.assertEqual(field_label("thickness_mm"), "thickness (mm)")
        self.assertEqual(field_label("capacity_l"), "capacity (L)")
        self.assertEqual(field_label("material"), "material")
        self.assertEqual((value_label("True"), value_label("False"), value_label("cotton")), ("Yes", "No", "cotton"))

    def test_a_yes_no_field_reads_yes_or_no(self):
        r = self.ask("wavefin tide speaker paani me chalega?")
        self.assertEqual(r.action, ANSWER)
        self.assertIn("water resistant: Yes", r.text)
        self.assertIn(("SKU-SP", "water_resistant", True), r.claims.facts)  # the structured fact keeps the raw field

    def test_a_missing_field_is_named_readably(self):
        r = self.ask("wavefin tide speaker anti slip?")  # another product has anti_slip; this one doesn't
        self.assertIn('does not list "anti slip" for the Wavefin Tide', r.text)

    def test_a_clarification_names_the_fields_readably(self):
        r = self.ask("yogix flow yoga mat price")
        self.assertEqual(r.action, CLARIFY)
        self.assertIn("thickness (mm)", r.text)
        self.assertIn("anti slip: No", r.text)


if __name__ == "__main__":
    unittest.main()
