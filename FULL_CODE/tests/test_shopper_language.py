"""How shoppers really type: typos inside category names, swapped or doubled letters, a word that is both a
material and a kind of product, a kind's own name read as an attribute question, their own names for things, and
policy questions whose heading wording differs from the body. A small pack-style catalogue and policy written here."""

import json
import tempfile
import unittest
from pathlib import Path

from groundtruth.answering import ANSWER, Session
from groundtruth.builder import build_assistant
from groundtruth.understanding.money import extract_money


def product(pid, title, sub, price, attrs=None):
    return {
        "product_id": pid,
        "title": title,
        "brand": title.split()[0],
        "category": "Test",
        "sub_category": sub,
        "description": f"{title}.",
        "attributes": attrs or {},
        "price_inr": price,
        "mrp_inr": price,
        "stock_status": "in_stock",
        "rating": 4.0,
        "review_count": 10,
    }


CATALOGUE = [
    product("SKU-RS1", "Runvex Zen Men's Running Shoes - White", "Running Shoes", 1999, {"colour": "white"}),
    product("SKU-SN1", "Runvex Urban Men's Sneakers - Black", "Sneakers", 1499, {"colour": "black"}),
    product("SKU-SH1", "Tanvika Zen Men's Cotton Formal Shirt - Blue", "Shirts", 899, {"colour": "blue"}),
    product("SKU-IR1", "Zestro Amber Dry Iron 1000 W - Teal", "Irons", 389, {"colour": "teal"}),
    product("SKU-TW1", "Gharkraft Ember Iron Tawa 28 cm", "Tawa", 419, {"material": "iron"}),
    product("SKU-SA1", "Saanjh Saawan Chiffon Saree - Black", "Sarees", 1299, {"saree_length_m": 5.5}),
    product("SKU-FN1", "Grindora Frost Wall Fan 400 mm - Brown", "Fans", 1899, {"fan_type": "wall"}),
    product("SKU-CB1", "Boltek Zen USB-C Cable 1 m - White", "USB Cables", 199, {"cable_type": "USB-C to USB-C"}),
    product("SKU-SP1", "Wavefin Tide Bluetooth Speaker - Green", "Bluetooth Speakers", 2199, {"water_resistant": True}),
    product("SKU-WB1", "Hydra Flow Steel Water Bottle 1 L", "Water Bottles", 499, {"material": "steel"}),
    product("SKU-LB1", "Rasoiya Mira Steel Lunch Box 3 Containers", "Lunch Boxes", 649, {"material": "steel"}),
    product("SKU-MG1", "Grindora Pulse Mixer Grinder 750 W", "Mixer Grinders", 3299, {"jars": 3}),
]

POLICY = """# Policy

## 1. Returns and Refunds

### 1.1 Return window
Most products can be returned within 30 days of delivery.

### 1.6 Exchanges
Apparel and footwear can be exchanged for a different size or colour of the same product.

## 2. Shipping and Delivery

### 2.1 Delivery charges
Delivery is free on orders of ₹499 or more. Orders below ₹499 have a delivery charge of ₹49.

### 2.2 Delivery timelines
Metro cities: usually 2-4 business days after the order is placed.
"""


class ShopperLanguageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp:
            cat, pol = Path(tmp) / "catalogue.jsonl", Path(tmp) / "policy.md"
            cat.write_text("".join(json.dumps(p) + "\n" for p in CATALOGUE), encoding="utf-8")
            pol.write_text(POLICY, encoding="utf-8")
            cls.a = build_assistant(cat, pol)

    def ask(self, question):
        return self.a.ask(question, Session())

    def assertLists(self, question, expected):
        r = self.ask(question)
        self.assertEqual(r.action, ANSWER, f"{question}: {r.text}")
        self.assertEqual(set(r.claims.products), expected, f"{question}: {r.text}")

    def test_a_typo_inside_a_category_name_still_finds_that_category(self):
        # "shoes" alone would read as Sneakers; the misspelt word completes Running Shoes instead
        self.assertLists("runniing shoes dikhao", {"SKU-RS1"})

    def test_swapped_and_doubled_letters_are_typos_too(self):
        self.assertLists("sihrts dikhao", {"SKU-SH1"})
        self.assertLists("runnning shoes", {"SKU-RS1"})

    def test_a_word_that_is_a_material_and_a_kind_names_the_kind(self):
        self.assertLists("irons dikhao", {"SKU-IR1"})
        self.assertLists("iron tawa", {"SKU-TW1"})  # with another kind named, iron is the material

    def test_naming_the_kind_is_not_an_attribute_question(self):
        r = self.ask("Saanjh Saawan black sarees kitne ka hai")  # not "saree length"
        self.assertEqual(r.claims.prices, [("SKU-SA1", 1299.0)])
        self.assertIn("SKU-CB1", self.ask("data cable dikhao").claims.products)  # "cable" also names cable_type

    def test_an_attribute_named_on_purpose_is_still_answered(self):
        self.assertIn("fan type", self.ask("grindora frost brown fan ka fan type kya hai").text)
        self.assertIn("water resistant: Yes", self.ask("wavefin tide speaker paani me chalega?").text)

    def test_shoppers_own_names_for_things(self):
        self.assertLists("tiffin chahiye", {"SKU-LB1"})
        self.assertLists("mixie dikhao", {"SKU-MG1"})

    def test_policy_topics_named_only_in_a_heading_are_found(self):
        for question, section in (
            ("exchange kar sakte hai kya", "1.6"),  # the body says "exchanged"
            ("delivery kitne din mein hogi", "2.2"),  # timelines, not the return sentence about days of delivery
            ("return kitne din me kar sakte hai", "1.1"),
            ("delivery charge kitna lagta hai", "2.1"),
        ):
            with self.subTest(question):
                r = self.ask(question)
                self.assertEqual((r.action, r.claims.sections), (ANSWER, [section]), r.text)

    def test_budgets_written_the_way_shoppers_write_them(self):
        for text in ("1500 rupees max", "1500/- tak", "1500 lopu", "1.5k lopala", "1500 varaku", "1500 kanna takkuva"):
            with self.subTest(text):
                self.assertEqual(extract_money(f"kurta {text}")[1:], (None, 1500.0))

    def test_telugu_and_hinglish_shopping_words(self):
        r = self.ask("runvex zen white running shoes entha")  # "how much"
        self.assertEqual(r.claims.prices, [("SKU-RS1", 1999.0)])
        self.assertLists("pankha dikhao", {"SKU-FN1"})  # Hindi for fan
        self.assertLists("running shoes 2500 lopu chupinchu", {"SKU-RS1"})  # "within 2500, show me"
        r = self.ask("enni rojulu lo delivery avutundi")  # "in how many days will it be delivered"
        self.assertEqual((r.action, r.claims.sections), (ANSWER, ["2.2"]))


if __name__ == "__main__":
    unittest.main()
