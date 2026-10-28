"""Edge cases: missing or empty data files, missing fields, and empty, huge or odd questions."""

import json
import tempfile
import time
import unittest
from pathlib import Path

import main
from groundtruth.answering import CLARIFY, REFUSE, Session
from groundtruth.builder import build_assistant
from groundtruth.catalogue import load_catalogue
from groundtruth.errors import DataFileError, GroundTruthError
from groundtruth.paths import ROOT
from groundtruth.policy import load_policy

POLICY = ROOT / "data" / "policy.txt"
CATALOGUE = ROOT / "data" / "catalogue.jsonl"


class DataFileTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name: str, text: str) -> Path:
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_missing_files_raise_a_data_file_error(self):
        with self.assertRaisesRegex(DataFileError, "catalogue file not found"):
            load_catalogue(self.tmp / "nope.jsonl")
        with self.assertRaisesRegex(DataFileError, "policy file not found"):
            load_policy(self.tmp / "nope.md")
        with self.assertRaisesRegex(DataFileError, "unsupported catalogue format"):
            load_catalogue(self.write("catalogue.xml", "<products/>"))

    def test_a_catalogue_with_no_readable_product_stops_start_up(self):
        for text in ("", '{"id": "A"}\nnot json\n'):
            with self.subTest(text=text), self.assertRaisesRegex(DataFileError, "no products could be read"):
                build_assistant(self.write("catalogue.jsonl", text), POLICY)

    def test_a_product_without_optional_fields_is_kept_and_unknowns_stay_unknown(self):
        row = {"id": "A1", "title": "Plain Steel Mug", "price": 199}
        [p] = load_catalogue(self.write("catalogue.jsonl", json.dumps(row) + "\n"))
        self.assertEqual((p.description, p.category, p.in_stock, p.rating, p.mrp), ("", "", None, None, None))
        a = build_assistant(self.write("one.jsonl", json.dumps(row) + "\n"), POLICY)
        reply = a.ask("is the plain steel mug in stock")
        self.assertIn("no stock status", reply.text)  # never assumed to be in stock
        self.assertEqual(reply.claims.facts, [])

    def test_an_empty_policy_refuses_policy_questions(self):
        a = build_assistant(CATALOGUE, self.write("policy.txt", ""))
        self.assertEqual(a.ask("what is the return policy").action, REFUSE)

    def test_main_reports_a_data_problem_in_one_line_with_exit_code_1(self):
        with self.assertLogs("groundtruth", level="ERROR") as logs:
            code = main.main(["evaluate", "--catalogue", str(self.tmp / "nope.jsonl")])
        self.assertEqual(code, 1)
        self.assertIn("catalogue file not found", logs.output[0])
        self.assertTrue(issubclass(DataFileError, GroundTruthError))


class OddQuestionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a = build_assistant(CATALOGUE, POLICY)

    def test_empty_or_blank_questions_get_a_prompt_not_an_error(self):
        for question in ("", "   ", "\n\t", "1234567890", "?!?!"):
            with self.subTest(question=question):
                self.assertEqual(self.a.ask(question, Session()).action, CLARIFY)

    def test_a_huge_question_is_answered_quickly(self):
        started = time.perf_counter()
        reply = self.a.ask("running shoes " * 3000, Session())
        self.assertLess(time.perf_counter() - started, 2.0)
        self.assertTrue(reply.claims.products)

    def test_markup_in_a_question_is_never_echoed_as_markup(self):
        reply = self.a.ask("<script>alert(1)</script> <img src=x onerror=alert(2)>", Session())
        self.assertNotIn("<", reply.text)
        self.assertEqual(reply.claims.prices, [])

    def test_non_latin_text_is_handled(self):
        for question in ("जूते दिखाओ", "👟👟", "చెప్పులు"):
            with self.subTest(question=question):
                self.assertIn(self.a.ask(question, Session()).action, (CLARIFY, REFUSE))


if __name__ == "__main__":
    unittest.main()
