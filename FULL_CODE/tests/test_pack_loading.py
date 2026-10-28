"""Reading the problem pack's formats: catalogue rows, the numbered Markdown policy, and the question files.

The fixtures mirror the pack's layout but are written here, so the tests run without the (git-ignored) pack.
"""

import json
import tempfile
import unittest
from pathlib import Path

from groundtruth.catalogue import load_catalogue
from groundtruth.evaluation.questions import conversations, load_questions, load_truth
from groundtruth.policy import load_policy

PACK_ROW = {
    "product_id": "SKU-008C8AQI",
    "title": "Threadcraft RippleDash Men's Fleece Jacket - Pink",
    "brand": "Threadcraft",
    "category": "Apparel",
    "sub_category": "Jackets",
    "description": "Threadcraft RippleDash jacket for cooler days.",
    "attributes": {"gender": "men", "hooded": True, "sizes": ["S", "M", "L"], "colour": "pink"},
    "price_inr": 1639,
    "mrp_inr": 4099,
    "stock_status": "low_stock",
    "rating": None,
    "review_count": 0,
}

POLICY_MD = """# Store Policies

Intro paragraph that is not a citable sub-section.

## 1. Returns and Refunds

### 1.1 Return window
Most products can be returned within **30 days of delivery**.

### 1.2 Free returns, no restocking fee
Returns are **free**.

## 2. Shipping and Delivery

### 2.1 Delivery charges
Delivery is **free on orders of ₹499 or more**. Orders below ₹499 have a delivery charge of **₹49**.
"""


def write(tmp: str, name: str, text: str) -> Path:
    path = Path(tmp) / name
    path.write_text(text, encoding="utf-8")
    return path


class PackCatalogueTests(unittest.TestCase):
    def test_pack_row_keeps_mrp_stock_status_and_original_key_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            [p] = load_catalogue(write(tmp, "catalogue.jsonl", json.dumps(PACK_ROW) + "\n"))
        self.assertEqual((p.price, p.mrp), (1639.0, 4099.0))
        self.assertEqual(p.stock_status, "low_stock")
        self.assertIs(p.in_stock, True)  # low stock can still be ordered
        self.assertIsNone(p.rating)  # no reviews: the catalogue gives no rating
        self.assertEqual(p.attr_values("color"), ["pink"])
        self.assertEqual(p.source_key("color"), "colour")
        self.assertEqual(p.source_key("size"), "sizes")
        self.assertEqual(p.source_key("sub_category"), "sub_category")
        self.assertEqual(p.attr_values("sub_category"), ["Jackets"])

    def test_csv_attributes_as_json_text_match_the_jsonl_form(self):
        row = {**PACK_ROW, "attributes": json.dumps(PACK_ROW["attributes"]), "rating": ""}
        header = list(row)
        line = ",".join(json.dumps(str(row[k])) if k == "attributes" else str(row[k]) for k in header)
        csv_text = ",".join(header) + "\n" + line.replace('\\"', '""') + "\n"
        with tempfile.TemporaryDirectory() as tmp:
            [p] = load_catalogue(write(tmp, "catalogue.csv", csv_text))
        self.assertEqual(p.attr_values("size"), ["S", "M", "L"])
        self.assertEqual(p.source_key("color"), "colour")


class MarkdownPolicyTests(unittest.TestCase):
    def setUp(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.sections = {s.id: s for s in load_policy(write(tmp, "policy.md", POLICY_MD))}

    def test_one_section_per_numbered_sub_section(self):
        self.assertEqual(list(self.sections), ["1.1", "1.2", "2.1"])
        self.assertEqual(self.sections["1.2"].heading, "Free returns, no restocking fee")

    def test_markdown_emphasis_is_removed_and_amounts_kept(self):
        body = self.sections["2.1"].body
        self.assertNotIn("**", body)
        self.assertEqual(
            body, "Delivery is free on orders of ₹499 or more. Orders below ₹499 have a delivery charge of ₹49."
        )


class QuestionFileTests(unittest.TestCase):
    HEADER = "question_id,conversation_id,turn,question_text\n"

    def test_turns_are_grouped_by_conversation_in_turn_order(self):
        rows = "Q2,C1,2,TPE wali 600 ke andar\nQ1,C1,1,6 mm yoga mat chahiye\nQ3,C2,1,redmi note 13 price\n"
        with tempfile.TemporaryDirectory() as tmp:
            convs = conversations(load_questions(write(tmp, "q.csv", self.HEADER + rows)))
        self.assertEqual([[q.question_id for q in c] for c in convs], [["Q1", "Q2"], ["Q3"]])
        self.assertEqual(convs[0][1].text, "TPE wali 600 ke andar")

    def test_missing_column_bad_turn_and_duplicate_id_are_rejected(self):
        cases = {
            "no column": "question_id,turn,question_text\nQ1,1,hi\n",
            "bad turn": self.HEADER + "Q1,C1,one,hi\n",
            "duplicate": self.HEADER + "Q1,C1,1,hi\nQ1,C2,1,hello\n",
        }
        for label, text in cases.items():
            with self.subTest(label), tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
                load_questions(write(tmp, "q.csv", text))

    def test_truth_rows_need_an_id_and_an_expected_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = write(tmp, "t.jsonl", json.dumps({"question_id": "Q1", "expected_action": "refuse"}) + "\n")
            self.assertEqual(load_truth(good)["Q1"]["expected_action"], "refuse")
            bad = write(tmp, "b.jsonl", json.dumps({"question_id": "Q1"}) + "\n")
            with self.assertRaises(ValueError):
                load_truth(bad)


if __name__ == "__main__":
    unittest.main()
