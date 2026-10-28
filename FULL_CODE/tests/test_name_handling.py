"""How shoppers name products: split line names, words that are part of a product's name, "rate" as price, and
stock questions about a named product line. A small pack-style catalogue written here."""

import json
import tempfile
import unittest
from pathlib import Path

from groundtruth.answering import ANSWER, CLARIFY, Session
from groundtruth.builder import build_assistant


def product(pid, title, sub, price, attrs, stock="in_stock"):
    return {
        "product_id": pid, "title": title, "brand": title.split()[0], "category": "Test", "sub_category": sub,
        "description": f"{title}.", "attributes": attrs, "price_inr": price, "mrp_inr": price,
        "stock_status": stock, "rating": 4.0, "review_count": 10,
    }  # fmt: skip


CATALOGUE = [
    product("SKU-SB1", "Trekora NimbusAir Kids' School Bag 26 L - Red", "School Bags", 899, {"colour": "red"}),
    product("SKU-HD1", "Herbique AmberAir 1000 W Hair Dryer - White", "Hair Dryers", 1199,
            {"wattage_w": 1000, "colour": "white"}),
    product("SKU-SH1", "Herbique SilkLeaf Shampoo 200 ml", "Shampoos", 299, {"hair_type": "oily", "volume_ml": 200}),
    product("SKU-CS3", "Sanchayo NimbusMax Steel Container Set of 3", "Containers", 899, {"containers": 3},
            stock="out_of_stock"),
    product("SKU-CS12", "Sanchayo NimbusMax Steel Container Set of 12", "Containers", 1979, {"containers": 12}),
    product("SKU-FB4", "Kridaa IonFlex Football Size 4 - Black", "Footballs", 499, {"ball_size": 4}),
    product("SKU-FB5", "Kridaa IonFlex Football Size 5 - Black", "Footballs", 599, {"ball_size": 5}),
    *[product(f"SKU-BP{i}", f"Yatrik Trail{i} Backpack 30 L - Grey", "Backpacks", 900 + i, {"colour": "grey"})
      for i in range(7)],
]  # fmt: skip


class NameHandlingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp:
            cat, pol = Path(tmp) / "catalogue.jsonl", Path(tmp) / "policy.md"
            cat.write_text("".join(json.dumps(p) + "\n" for p in CATALOGUE), encoding="utf-8")
            pol.write_text("# Policy\n\n## 1. Returns\n\n### 1.1 Return window\nReturns within 30 days.\n")
            cls.a = build_assistant(cat, pol)

    def ask(self, question):
        return self.a.ask(question, Session())

    def test_a_line_name_split_in_two_is_rejoined_even_when_it_ends_in_s(self):
        r = self.ask("trekora nimbus air red school bags kitne ka hai")  # "nimbus" would stem to "nimbu"
        self.assertEqual((r.action, r.claims.prices), (ANSWER, [("SKU-SB1", 899.0)]))

    def test_school_bags_stay_school_bags_when_the_catalogue_sells_them(self):
        self.assertIn("SKU-SB1", self.ask("red school bags").claims.products)

    def test_a_word_in_the_product_name_does_not_ask_about_that_attribute(self):
        r = self.ask("herbique amberair white hair dryers kitne ka hai")  # "hair" is not a hair_type question
        self.assertEqual((r.action, r.claims.prices), (ANSWER, [("SKU-HD1", 1199.0)]))

    def test_rate_asks_the_price(self):
        r = self.ask("kridaa ionflex football rate?")  # two sizes at different prices
        self.assertEqual(r.action, CLARIFY)
        self.assertEqual(set(r.claims.products), {"SKU-FB4", "SKU-FB5"})

    def test_a_stock_question_about_a_named_line_asks_which_one(self):
        r = self.ask("Sanchayo NimbusMax steel container set stock me hai?")  # one in stock, one not
        self.assertEqual(r.action, CLARIFY)
        self.assertEqual(set(r.claims.products), {"SKU-CS3", "SKU-CS12"})

    def test_a_broad_stock_question_still_lists(self):
        self.assertEqual(self.ask("backpacks in stock?").action, ANSWER)


if __name__ == "__main__":
    unittest.main()
