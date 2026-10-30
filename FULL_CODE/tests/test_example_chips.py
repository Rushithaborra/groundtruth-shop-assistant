"""The demo's example chips and product cards name a product by what it is ("running shoes"), not by the last word
of its title: the pack's titles end in a colour ("... Running Shoes - Blue"), which made a chip read "the white white".
A small pack-style catalogue written here."""

import json
import tempfile
import unittest
from pathlib import Path

from api.views import build_examples, noun_of, product_nouns, product_record
from groundtruth.builder import build_assistant

BRANDS = ("Padvi", "Stepzo", "Trailnest", "Kicksy")


def shoe(n, colour="Blue", material="mesh"):
    title = f"{BRANDS[n]} {['AmberFlex', 'AstraZip', 'UltraLoop', 'Glidera'][n]} Men's Running Shoes - {colour}"
    return {
        "product_id": f"SKU-{n}",
        "title": title,
        "brand": BRANDS[n],
        "category": "Footwear",
        "sub_category": "Running Shoes",
        "description": f"{title}.",
        "attributes": {"colour": colour.lower(), "material": material},
        "price_inr": 1500 + 100 * n,
        "mrp_inr": 2000,
        "stock_status": "in_stock",
        "rating": 4.0,
        "review_count": 10,
    }


POLICY = "# Policy\n\n## 1. Returns\n\n### 1.1 Return window\nReturns within 30 days.\n"


class ExampleChipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as tmp:
            cat, pol = Path(tmp) / "catalogue.jsonl", Path(tmp) / "policy.md"
            cat.write_text("".join(json.dumps(shoe(n)) + "\n" for n in range(4)), encoding="utf-8")
            pol.write_text(POLICY, encoding="utf-8")
            cls.assistant = build_assistant(cat, pol)
        cls.index = cls.assistant.index

    def test_the_sub_category_names_the_product_not_the_trailing_colour(self):
        self.assertEqual(product_nouns(self.index)[0][0], "running shoes")
        record = product_record(self.index.products["SKU-0"])
        self.assertEqual(record["noun"], "Running Shoes")

    def test_the_trap_chip_names_the_product(self):
        chips = [e["q"] for e in build_examples(self.assistant) if e["label"] == "Trap"]
        self.assertEqual(chips, ["price of the blue running shoes"])

    def test_without_a_sub_category_a_trailing_colour_is_still_not_the_noun(self):
        kinds = frozenset({"running", "shoe"})  # words that name a kind of product
        self.assertEqual(noun_of("Padvi AmberFlex Running Shoes - Blue", kinds), "Running Shoes")
        self.assertEqual(noun_of("Padvi AmberFlex Running Shoes (Blue)", kinds), "Running Shoes")


if __name__ == "__main__":
    unittest.main()
