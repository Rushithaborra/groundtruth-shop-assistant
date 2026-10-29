"""The shopper-language set: reproducible, in the official truth format, expectations from the catalogue, and halves
that share no wording (so tuning on one can't raise the other)."""

import random
import unittest

from tools import shoppers

REQUIRED = {
    "question_id",
    "expected_action",
    "acceptable_product_ids",
    "required_facts",
    "clarify_candidate_ids",
    "policy_sections",
    "question_text",
    "part",
}


def product(pid, line, sub, price, colour="blue"):
    return {
        "product_id": pid,
        "title": f"Runvex {line} {sub} - {colour.title()}",
        "brand": "Runvex",
        "sub_category": sub,
        "price_inr": price,
        "stock_status": "in_stock",
        "attributes": {"colour": colour},
    }


CATALOGUE = [product(f"SKU-{i}", f"Line{i}", "Running Shoes", 900 + 100 * i) for i in range(10)] + [
    product(f"SKU-L{i}", f"Box{i}", "Lunch Boxes", 300 + 50 * i, "red") for i in range(5)
]


class ShoppersToolTests(unittest.TestCase):
    def test_rows_are_reproducible_and_in_the_official_truth_format(self):
        rows = shoppers.build(CATALOGUE)
        self.assertEqual(rows, shoppers.build(CATALOGUE))
        self.assertEqual({r["part"] for r in rows}, {"tune", "holdout"})
        for r in rows:
            self.assertLessEqual(REQUIRED, set(r))

    def test_expectations_come_from_the_catalogue(self):
        rows = shoppers.build(CATALOGUE)
        prices = {p["product_id"]: p["price_inr"] for p in CATALOGUE}
        cheapest = [r for r in rows if r["question_type"] == "answer_superlative" and "running" in r["question_text"]]
        self.assertEqual(cheapest[0]["acceptable_product_ids"], ["SKU-0"])
        tiffin = next(r for r in rows if r["question_text"].startswith("tiffin "))
        self.assertEqual(set(tiffin["acceptable_product_ids"]), {f"SKU-L{i}" for i in range(5)})
        for r in rows:
            n = next((int(w) for w in r["question_text"].split() if w.isdigit()), None)
            if r["question_type"] == "answer_filter" and n:  # a budget listing: everything acceptable fits it
                self.assertTrue(all(prices[pid] <= n for pid in r["acceptable_product_ids"]), r)

    def test_the_halves_share_no_wording(self):
        for table in (shoppers.BUDGET, shoppers.CHEAPEST, shoppers.LOOKUP, shoppers.BROWSE, shoppers.NOT_SOLD_ASKS):
            self.assertFalse(set(table["tune"]) & set(table["holdout"]))
        for table in (shoppers.OTHER_NAMES, shoppers.NOT_SOLD):
            self.assertFalse(set(table["tune"]) & set(table["holdout"]))
        tune_policy = {text for text, _ in shoppers.POLICY["tune"]}
        self.assertFalse(tune_policy & {text for text, _ in shoppers.POLICY["holdout"]})

    def test_a_typo_is_one_slip(self):
        rng = random.Random(1)
        slips = {shoppers._typo("running shoes", rng) for _ in range(30)}
        self.assertGreater(len(slips - {"running shoes"}), 5)  # swapping a doubled letter can give the word back,
        for slipped in slips:  # and the generator skips those
            self.assertLessEqual(abs(len(slipped) - len("running shoes")), 1)
            self.assertTrue(slipped.endswith(" shoes"))  # only the longest word is changed


if __name__ == "__main__":
    unittest.main()
