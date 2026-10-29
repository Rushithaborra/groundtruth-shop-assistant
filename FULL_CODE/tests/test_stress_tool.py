"""The stress-set generator: reproducible, in the official truth format, with expectations from the catalogue."""

import random
import unittest

from tools.stress import _drop_letter, _split_line, build

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


def product(pid, line, colour, price, sub="Running Shoes", **attrs):
    return {
        "product_id": pid,
        "title": f"Runvex {line} Men's Running Shoes - {colour.title()}",
        "brand": "Runvex",
        "sub_category": sub,
        "price_inr": price,
        "stock_status": "in_stock",
        "attributes": {"colour": colour, **attrs},
    }


CATALOGUE = [
    product(f"SKU-{i}", f"Line{i % 7}Flex", colour, 1000 + 37 * i, material="mesh")
    for i, colour in enumerate(["red", "blue", "green", "black", "white", "grey"] * 8)
]


class StressToolTests(unittest.TestCase):
    def test_rows_are_reproducible_and_in_the_official_truth_format(self):
        rows = build(CATALOGUE, n_products=30)
        self.assertEqual(rows, build(CATALOGUE, n_products=30))
        self.assertTrue(rows)
        for row in rows:
            self.assertLessEqual(REQUIRED, set(row))
        self.assertEqual({r["part"] for r in rows}, {"tune", "holdout"})

    def test_expected_behaviour_follows_from_the_catalogue(self):
        prices = {p["product_id"]: p["price_inr"] for p in CATALOGUE}
        for row in build(CATALOGUE, n_products=30):
            if row["expected_action"] == "clarify":
                self.assertGreater(len({prices[pid] for pid in row["clarify_candidate_ids"]}), 1)
            for fact in row["required_facts"]:
                if fact["field"] == "price_inr":
                    self.assertEqual(fact["value"], prices[fact["product_id"]])

    def test_name_perturbations(self):
        self.assertEqual(_split_line("RippleFlex"), "ripple flex")
        typo = _drop_letter("Kridaa", random.Random(1))
        self.assertEqual(len(typo), 5)
        self.assertEqual(_drop_letter("Zen", random.Random(1)), "Zen")


if __name__ == "__main__":
    unittest.main()
