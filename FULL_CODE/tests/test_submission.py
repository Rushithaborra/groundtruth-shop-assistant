"""The submission file: one valid line per question in the pack's format, built from the replies' claims."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from groundtruth.builder import build_assistant
from groundtruth.evaluation.questions import load_questions
from groundtruth.evaluation.submission import REQUIRED_KEYS, predict, validate_predictions, write_predictions
from groundtruth.paths import ROOT

QUESTIONS = """question_id,conversation_id,turn,question_text
Q-1,C-1,1,running shoes
Q-2,C-1,2,under 2500
Q-3,C-2,1,do you sell iPhones?
Q-4,C-3,1,what is the return policy
Q-5,C-4,1,hi
"""


class SubmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a = build_assistant(ROOT / "data" / "catalogue.jsonl", ROOT / "data" / "policy.txt")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "questions.csv"
            path.write_text(QUESTIONS, encoding="utf-8")
            cls.questions = load_questions(path)
        cls.rows = predict(cls.a, cls.questions)

    def test_one_line_per_question_in_file_order_with_every_required_key(self):
        self.assertEqual([r["question_id"] for r in self.rows], ["Q-1", "Q-2", "Q-3", "Q-4", "Q-5"])
        for row in self.rows:
            self.assertEqual(set(REQUIRED_KEYS), set(row))
        validate_predictions(self.rows, self.questions)

    def test_prices_and_citations_come_from_the_catalogue(self):
        products = self.a.index.products
        listing = self.rows[0]
        self.assertEqual(listing["action"], "answer")
        self.assertTrue(listing["stated_prices"])
        for price in listing["stated_prices"]:
            self.assertIn(price["product_id"], listing["cited_product_ids"])
            self.assertEqual(price["price_inr"], products[price["product_id"]].price)
            self.assertIsInstance(price["price_inr"], int)  # whole rupees, as in the catalogue

    def test_a_follow_up_turn_keeps_the_conversation_constraints(self):
        products = self.a.index.products
        follow_up = self.rows[1]
        self.assertTrue(follow_up["cited_product_ids"])
        for pid in follow_up["cited_product_ids"]:
            self.assertLessEqual(products[pid].price, 2500)
            self.assertIn("shoe", products[pid].title.lower())

    def test_refusal_and_policy_lines(self):
        refusal, policy = self.rows[2], self.rows[3]
        self.assertEqual((refusal["action"], refusal["stated_prices"]), ("refuse", []))
        self.assertEqual(policy["action"], "answer")
        self.assertEqual(policy["cited_policy_sections"], ["returns"])

    def test_validation_rejects_missing_duplicate_and_malformed_lines(self):
        broken = {
            "missing line": self.rows[:-1],
            "duplicate line": self.rows + [self.rows[0]],
            "missing key": [{k: v for k, v in r.items() if k != "tokens_in"} for r in self.rows],
            "bad action": [{**r, "action": "maybe"} for r in self.rows],
            "negative latency": [{**r, "latency_ms": -1} for r in self.rows],
            "price for an uncited product": [
                {**r, "cited_product_ids": []} if r["stated_prices"] else r for r in copy.deepcopy(self.rows)
            ],
        }
        for label, rows in broken.items():
            with self.subTest(label), self.assertRaises(ValueError):
                validate_predictions(rows, self.questions)

    def test_written_file_is_utf8_json_lines_that_read_back_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_predictions(self.rows, Path(tmp) / "sub" / "R3_predictions.jsonl")
            lines = path.read_text(encoding="utf-8").splitlines()
        self.assertEqual([json.loads(line) for line in lines], self.rows)


if __name__ == "__main__":
    unittest.main()
