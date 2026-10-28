"""Structured claims: what a reply records (citations, prices, facts) must match what its text says, and every
recorded value must be the catalogue's own value under the catalogue's own field name."""

import json
import re
import unittest

from groundtruth.answering import Session
from groundtruth.builder import build_assistant
from groundtruth.paths import ROOT

CITE = re.compile(r"\[([^\]]+)\]")
RUPEES = re.compile(r"₹\s?(\d[\d,]*(?:\.\d+)?)")
NUMBERS = re.compile(r"\d[\d,]*(?:\.\d+)?")


def conversations() -> list[list[str]]:
    convs = []
    for name in ("test_questions.jsonl", "hard_questions.jsonl"):
        for line in (ROOT / "data" / name).read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                convs.append(row.get("turns") or [row["question"]])
    return convs


class ClaimsMatchTextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a = build_assistant(ROOT / "data" / "catalogue.jsonl", ROOT / "data" / "policy.txt")
        cls.replies = []
        for turns in conversations():
            session = Session()
            for i, turn in enumerate(turns):
                cls.replies.append((turns[: i + 1], cls.a.ask(turn, session)))
        assert len(cls.replies) > 100

    def test_recorded_citations_are_exactly_the_text_citations(self):
        for turns, r in self.replies:
            cited = CITE.findall(r.text)
            with self.subTest(query=turns[-1]):
                self.assertEqual(set(r.claims.products), {c for c in cited if not c.startswith("policy:")})
                self.assertEqual(set(r.claims.sections), {c[7:] for c in cited if c.startswith("policy:")})

    def test_recorded_prices_are_catalogue_prices_and_cover_every_amount_in_the_text(self):
        products = self.a.index.products
        for turns, r in self.replies:
            with self.subTest(query=turns[-1]):
                for pid, price in r.claims.prices:
                    self.assertEqual(price, products[pid].price)
                shopper = {float(n.replace(",", "")) for n in NUMBERS.findall(" ".join(turns))}
                policy = {
                    float(a.replace(",", ""))
                    for sid in r.claims.sections
                    for a in RUPEES.findall(self.a.sections[sid].body)
                }
                allowed = {price for _, price in r.claims.prices} | shopper | policy
                for amount in RUPEES.findall(r.text):
                    self.assertIn(float(amount.replace(",", "")), allowed)

    def test_recorded_facts_hold_the_catalogue_value(self):
        products = self.a.index.products
        for turns, r in self.replies:
            for pid, field, value in r.claims.facts:
                p = products[pid]
                with self.subTest(query=turns[-1], field=field):
                    self.assertIn(pid, r.claims.products)
                    if field == "stock_status":
                        self.assertEqual(value, p.stock_status)
                    elif field == "rating":
                        self.assertEqual(value, p.rating)
                    elif field == "review_count":
                        self.assertEqual(value, p.rating_count)
                    else:
                        key = next(k for k in p.attributes if p.source_key(k) == field)
                        shown = p.attributes[key]
                        self.assertTrue(value == shown or (isinstance(shown, list) and value in shown))

    def test_a_plain_greeting_records_nothing(self):
        r = self.a.ask("hi")
        self.assertEqual((r.claims.products, r.claims.prices, r.claims.facts), ([], [], []))


if __name__ == "__main__":
    unittest.main()
