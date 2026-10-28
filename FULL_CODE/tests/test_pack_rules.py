"""The pack's answering rules, on a small pack-style catalogue built here (no dependency on the ignored pack).

Covers: measurements as constraints, size words that are other attributes, "sabse ... jo stock me ho", attribute
questions in Hinglish or by multi-word name, refusing when the catalogue doesn't list a fact, sub-categories that
read the same, and the policy's named terms.
"""

import json
import tempfile
import unittest
from pathlib import Path

from groundtruth.answering import ANSWER, CLARIFY, REFUSE
from groundtruth.builder import build_assistant
from groundtruth.understanding.measures import extract_measures


def product(pid, title, sub, price, attrs, stock="in_stock", brand=None):
    return {
        "product_id": pid, "title": title, "brand": brand or title.split()[0], "category": "Test", "sub_category": sub,
        "description": f"{title}.", "attributes": attrs, "price_inr": price, "mrp_inr": price,
        "stock_status": stock, "rating": 4.0, "review_count": 10,
    }  # fmt: skip


CATALOGUE = [
    product("SKU-C65A", "Voltix Rapid 65 W Charger - White", "Chargers", 1299, {"output_w": 65, "colour": "white"}),
    product("SKU-C65B", "Voltix Rapid 65 W Charger - Black", "Chargers", 1349, {"output_w": 65, "colour": "black"}),
    product("SKU-C30A", "Voltix Rapid 30 W Charger - White", "Chargers", 699, {"output_w": 30, "colour": "white"}),
    product("SKU-BK1", "Sootli Frost Cotton King Bedsheet - Pink", "Bedsheets", 439,
            {"material": "cotton", "bed_size": "king", "colour": "pink"}, stock="out_of_stock"),
    product("SKU-BK2", "Sootli Frost Cotton King Bedsheet - Blue", "Bedsheets", 759,
            {"material": "cotton", "bed_size": "king", "colour": "blue"}),
    product("SKU-BK3", "Sootli Frost Cotton King Bedsheet - Grey", "Bedsheets", 899,
            {"material": "cotton", "bed_size": "king", "colour": "grey"}),
    product("SKU-BD1", "Sootli Frost Cotton Double Bedsheet - Pink", "Bedsheets", 359,
            {"material": "cotton", "bed_size": "double", "colour": "pink"}),
    product("SKU-SP1", "Wavefin Sage X2 Speaker - Purple", "Bluetooth Speakers", 1699, {"colour": "purple"}),
    product("SKU-SP2", "Wavefin Tide X3 Speaker - Green", "Bluetooth Speakers", 2199,
            {"colour": "green", "water_resistant": True}),
    product("SKU-YM1", "Strikeforce Vista PVC Yoga Mat - Teal", "Yoga Mats", 719,
            {"thickness_mm": 10, "colour": "teal"}),
    product("SKU-DF1", "Yatrik Vega Duffel Bag - Brown", "Duffel Bags", 1899,
            {"shoe_compartment": True, "colour": "brown", "material": "polyester"}),
    product("SKU-KT1", "Threadcraft Chandni Cotton Kurti - Olive", "Kurtis", 599, {"colour": "olive"}),
    product("SKU-KT2", "Saanjh Chandni Silk Kurta - Grey", "Kurtas", 1099, {"colour": "grey"}),
]  # fmt: skip

POLICY = """# Policy

## 7. Prices

### 7.1 Selling price and MRP
The price shown is the selling price, inclusive of GST.

## 1. Returns

### 1.2 Free returns
Returns are free and there is no restocking fee.
"""


class PackRuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp:
            cat, pol = Path(tmp) / "catalogue.jsonl", Path(tmp) / "policy.md"
            cat.write_text("".join(json.dumps(p) + "\n" for p in CATALOGUE), encoding="utf-8")
            pol.write_text(POLICY, encoding="utf-8")
            cls.a = build_assistant(cat, pol)

    def test_measurements_are_read_with_their_units(self):
        self.assertEqual(extract_measures("65W charger")[1], [("w", 65.0)])
        self.assertEqual(extract_measures("5.5 litre air fryer")[1], [("l", 5.5)])
        self.assertEqual(extract_measures("100ml face wash, 500 piece puzzle")[1], [("ml", 100.0), ("pieces", 500.0)])

    def test_a_measurement_constrains_the_listing(self):
        r = self.a.ask("65W charger dikhao")
        self.assertEqual(r.action, ANSWER)
        self.assertEqual(set(r.claims.products), {"SKU-C65A", "SKU-C65B"})

    def test_king_size_is_a_bed_size_and_the_cheapest_in_stock_one_is_named(self):
        r = self.a.ask("king size cotton bedsheet sabse sasti jo stock me ho")
        self.assertEqual(r.action, ANSWER)
        self.assertEqual(r.claims.products, ["SKU-BK2"])  # BK1 is cheaper but out of stock; BD1 is a double
        self.assertEqual(r.claims.prices, [("SKU-BK2", 759.0)])

    def test_a_fact_the_catalogue_doesnt_list_is_refused(self):
        r = self.a.ask("Wavefin Sage X2 purple speaker paani me chalega?")
        self.assertEqual(r.action, REFUSE)
        self.assertEqual(r.claims.prices, [])

    def test_a_listed_fact_is_answered_under_its_catalogue_name(self):
        r = self.a.ask("Wavefin Tide X3 green speaker paani me chalega?")
        self.assertEqual(r.action, ANSWER)
        self.assertIn(("SKU-SP2", "water_resistant", True), r.claims.facts)

    def test_hinglish_attribute_word_asks_about_the_attribute_not_the_price(self):
        r = self.a.ask("Strikeforce Vista teal mat kitni moti hai")
        self.assertEqual(r.action, ANSWER)
        self.assertIn(("SKU-YM1", "thickness_mm", 10), r.claims.facts)
        self.assertEqual(r.claims.prices, [])

    def test_a_multi_word_attribute_name_is_not_a_product_type(self):
        r = self.a.ask("yatrik vega brown duffel me shoe compartment hai?")
        self.assertEqual(r.action, ANSWER)
        self.assertIn(("SKU-DF1", "shoe_compartment", True), r.claims.facts)

    def test_sub_categories_that_read_the_same_are_both_matched(self):
        r = self.a.ask("Chandni kurti kitne ki hai")  # a Kurti and a Kurta, at different prices
        self.assertEqual(r.action, CLARIFY)
        self.assertEqual(set(r.claims.products), {"SKU-KT1", "SKU-KT2"})

    def test_a_named_policy_term_alone_finds_the_policy(self):
        r = self.a.ask("GST alag se lagega kya")
        self.assertEqual((r.action, r.claims.sections), (ANSWER, ["7.1"]))

    def test_an_ordinary_word_alone_does_not(self):
        self.assertNotEqual(self.a.ask("delivery boy tip kitna").claims.sections, ["1.2"])


if __name__ == "__main__":
    unittest.main()
