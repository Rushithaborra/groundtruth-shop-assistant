"""Patterns found in the first questions from real shoppers (the brief's Task 4), each as a general rule: the pack's
rule 6.2 (answer when every match gives the same answer, refuse when none records it, clarify only when they differ),
filler words that don't refuse a named product, a value read against the kind's own attribute, the most specific
kind, common words of other kinds made soft but names kept hard, sizes, refinements, and policy "how to" questions.
A small pack-style catalogue and policy written here."""

import json
import tempfile
import unittest
from pathlib import Path

from groundtruth.answering import ANSWER, CLARIFY, REFUSE, Session
from groundtruth.builder import build_assistant


def product(pid, title, sub, price, attrs=None, stock="in_stock"):
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
        "stock_status": stock,
        "rating": 4.0,
        "review_count": 10,
    }


CATALOGUE = [
    product("PB1", "Sonique Nimbus 10000 mAh Power Bank - Green", "Power Banks", 799, {"capacity_mah": 10000}),
    product(
        "PB2", "Boltek Zip 10000 mAh Power Bank - Black", "Power Banks", 999, {"capacity_mah": 10000}, "out_of_stock"
    ),
    product("SS1", "Tulsiya Frost Sunscreen SPF 50 100 ml", "Sunscreen", 449, {"skin_type": "oily"}),
    product("SS2", "Tulsiya Glow Sunscreen SPF 30 100 ml", "Sunscreen", 399, {"skin_type": "dry"}),
    product("SH1", "Herbique Leaf Shampoo 200 ml", "Shampoo", 299, {"hair_type": "oily"}),
    product("WS1", "Padvi Amber Men's Walking Shoes - Grey", "Walking Shoes", 899, {"sizes": ["UK 8", "UK 9"]}),
    product("WS2", "Padvi Breeze Men's Walking Shoes - Navy", "Walking Shoes", 949, {"sizes": ["UK 6", "UK 7"]}),
    product("SN1", "Runvex Urban Men's Sneakers - White", "Sneakers", 799, {"sizes": ["UK 9"]}),
    product("PO1", "Saanjh Ion Unisex Polo T-Shirt - Black", "T-Shirts", 589, {"colour": "black"}),
    product("PO2", "Kesariya Trek Men's Polo T-Shirt - Black", "T-Shirts", 299, {"colour": "black"}),
    product("IR1", "Pavana Astra Dry Iron 1200 W - White", "Irons", 649, {"wattage_w": 1200}),
    product("IR2", "Zestro Amber Dry Iron 1000 W - Teal", "Irons", 389, {"wattage_w": 1000}),
    product("DB1", "Kridaa Core Dumbbell 5 kg", "Dumbbells", 999, {"weight_kg": 5}),
    product("TF1", "Grindora Urban Table Fan 300 mm - Black", "Fans", 1299, {"fan_type": "table", "speed_settings": 3}),
    product("TF2", "Grindora Echo Table Fan 400 mm - White", "Fans", 1499, {"fan_type": "table", "speed_settings": 3}),
    product("LB1", "Mistique Zen Lip Balm 4.5 g", "Lip Balm", 99, {"spf": 15}),
    product("LB2", "Mistique Rose Lip Balm 4.5 g", "Lip Balm", 109, {"spf": 20}),
    product("LS1", "Trekora Pulse Laptop Sleeve 14 inch - Navy", "Laptop Sleeves", 699),
    product("BP1", "Yatrik Trail Backpack 30 L - Pink", "Backpacks", 749, {"colour": "pink"}),
    product("BP2", "Yatrik Peak Backpack 35 L - Grey", "Backpacks", 1299, {"colour": "grey"}),
    product("WB1", "Sanchayo Nimbus Steel Water Bottle 1 L - Grey", "Water Bottles", 499, {"colour": "grey"}),
    product("SL1", "Stepzo Aero Men's Slippers - Black", "Flip-Flops & Slippers", 119, {"colour": "black"}),
    product("SL2", "Padvi Vega Men's Slippers - Black", "Flip-Flops & Slippers", 319, {"colour": "black"}),
    product("SD1", "Kadambh Dune Women's Sandals - Black", "Sandals", 599, {"colour": "black"}),
    # "gym" is a common word in a real catalogue (dozens of gym gloves), not the name of a product line
    *[product(f"GG{i}", f"Kridaa Grip{i} Gym Gloves - Black", "Gym Gloves", 300 + i) for i in range(20)],
]

POLICY = """# Policy

## 1. Returns

### 1.1 Return window
Most products can be returned within 30 days of delivery.

### 1.3 Condition of returned items
Returned items must be unused, with original tags and packaging.

### 1.4 How to start a return
Go to My Orders, choose the item and select a return reason.

## 6. Non-returnable Items

### 6.1 Beauty and personal care products
Beauty products cannot be returned once the seal is opened.
"""


class RealShopperTests(unittest.TestCase):
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

    # the pack's rule 6.2
    def test_same_answer_for_every_match_is_answered(self):
        self.assertEqual(self.ask("Black polo tshirt undha?").action, ANSWER)  # both in stock
        r = self.ask("Does this table fan have speed control?")  # every table fan has 3 speed settings
        self.assertEqual((r.action, set(r.claims.products)), (ANSWER, {"TF1", "TF2"}))

    def test_a_fact_no_match_records_is_refused(self):
        self.assertEqual(self.ask("Iron ka weight kitna hai?").action, REFUSE)  # only dumbbells list a weight

    def test_different_answers_still_clarify(self):
        r = self.ask("Is this powerbank available rn?")  # one in stock, one not; "rn" is just filler
        self.assertEqual((r.action, set(r.claims.products)), (CLARIFY, {"PB1", "PB2"}))
        r = self.ask("Black slippers ki price entha?")  # slippers are their own kind, not sandals
        self.assertEqual(set(r.claims.products), {"SL1", "SL2"})

    # rule 6.3: a question about something the catalogue doesn't record
    def test_a_question_the_catalogue_cannot_answer_is_refused(self):
        self.assertEqual(self.ask("Is the lip balm good for daily use?").action, REFUSE)
        self.assertEqual(self.ask("Can I get a laptop here?").action, REFUSE)  # not the laptop sleeves
        self.assertEqual(self.ask("laptop sleeve dikhao").action, ANSWER)

    # reading the message against the kind it names
    def test_a_value_goes_to_the_attribute_the_kind_has(self):
        r = self.ask("Suggest a sunscreen for oily skin under 500.")  # skin_type, not the shampoo's hair_type
        self.assertEqual((r.action, r.claims.products), (ANSWER, ["SS1"]))

    def test_the_most_specific_kind_wins(self):
        r = self.ask("Suggest some good shoes under 1000 for daily walking.")
        self.assertEqual(r.action, ANSWER)
        self.assertTrue(set(r.claims.products) <= {"WS1", "WS2"}, r.text)  # walking shoes, not sneakers

    def test_a_common_word_of_another_kind_is_soft(self):
        r = self.ask("I need a water bottle for gym, preferably grey or black.")
        self.assertEqual((r.action, r.claims.products), (ANSWER, ["WB1"]))

    # sizes and refinements
    def test_size_9_means_uk_9(self):
        r = self.ask("Are these walking shoes available in size 9?")
        self.assertEqual(r.claims.products, ["WS1"])

    def test_a_refinement_with_filler_words_is_not_refused(self):
        r = self.ask("Show me some backpacks.", "budget 800 lopu undali")
        self.assertEqual((r.action, r.claims.products), (ANSWER, ["BP1"]))

    def test_how_much_is_it_after_a_listing_asks_which_one(self):
        r = self.ask("I want to buy a powerbank.", "how much is it?")  # not "You're welcome"
        self.assertEqual((r.action, set(r.claims.products)), (CLARIFY, {"PB1", "PB2"}))

    # policy
    def test_how_to_questions_find_the_how_to_section(self):
        r = self.ask("How can I return an item?")
        self.assertEqual((r.action, r.claims.sections), (ANSWER, ["1.4"]))
        r = self.ask("package open chesaka return cheyocha?")  # "package" is not "pack" + "age"
        self.assertEqual((r.action, r.claims.sections), (ANSWER, ["6.1"]))


if __name__ == "__main__":
    unittest.main()
