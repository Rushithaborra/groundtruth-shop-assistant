"""Regression tests for vocabulary and routing fixes found with independently written queries.

Run: python -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from evaluate import Grounding  # noqa: E402
from shopassistant import ANSWER, CLARIFY, REFUSE, Session, build_assistant  # noqa: E402
from shopassistant.text import tokenize  # noqa: E402


class VocabularyFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a = build_assistant(ROOT / "data" / "catalogue.jsonl", ROOT / "data" / "policy.txt")
        cls.g = Grounding(cls.a)
        cls.products = cls.a.index.products

    def ask(self, *turns):
        s = Session()
        for t in turns:
            r = self.a.ask(t, s)
        self.assertFalse(self.g.check(r.text, " ".join(turns))["problems"], r.text)  # every reply stays grounded
        return r

    def cited_products(self, r):
        return [self.products[c] for c in r.citations if c in self.products]

    # -- policy ----------------------------------------------------------------------------------------
    def test_single_keyword_refund_question_is_answered(self):
        for q in ("how long does refund take", "refund kab milega"):
            r = self.ask(q)
            self.assertEqual(r.action, ANSWER, q)
            self.assertIn("7 business days", r.text)
            self.assertIn("[policy:returns]", r.text)

    def test_cancel_matches_cancelled(self):
        r = self.ask("order cancel kaise kare")
        self.assertEqual(r.action, ANSWER)
        self.assertIn("[policy:cancellations]", r.text)

    def test_foreign_place_gets_outside_india_sentence(self):
        for q in ("delivery to dubai?", "do you ship to usa", "international shipping?"):
            r = self.ask(q)
            self.assertEqual(r.action, ANSWER, q)
            self.assertIn("outside India", r.text, q)

    def test_delivery_time_comes_from_shipping_not_returns(self):
        r = self.ask("kitne din mein delivery hogi")
        self.assertIn("3 to 5 business days", r.text)

    def test_wrong_size_routes_to_exchanges(self):
        r = self.ask("size galat aaya toh exchange ho jayega?")
        self.assertIn("[policy:exchanges]", r.text)

    def test_policy_about_brand_not_sold_says_so_first(self):
        r = self.ask("what's the warranty on samsung tv")
        self.assertEqual(r.action, REFUSE)
        self.assertTrue(r.text.startswith("I don't have Samsung"))

    # -- sizes -----------------------------------------------------------------------------------------
    def test_spoken_sizes_map_to_catalogue_sizes(self):
        for q in ("black tshirt medium size", "M size black t shirt", "tshirt size medium"):
            r = self.ask(q)
            self.assertEqual(r.action, ANSWER, q)
            for p in self.cited_products(r):
                self.assertIn("M", p.attr_values("size"), q)

    def test_bedding_size_words(self):
        r = self.ask("bedsheet king size")
        self.assertEqual(r.action, ANSWER)
        for p in self.cited_products(r):
            self.assertIn("king", p.attr_values("size"))

    def test_explicit_size_not_stocked_is_refused(self):
        self.assertEqual(self.ask("blue running shoes size 50").action, REFUSE)

    def test_bare_size_word_ignored_for_unsized_products(self):
        self.assertEqual(self.ask("large water bottle").action, ANSWER)

    # -- product vocabulary ----------------------------------------------------------------------------
    def test_modifier_words_do_not_cause_refusal(self):
        for q in ("sneakers for gym", "shoes for jogging under 3k", "hoodie for winter"):
            self.assertEqual(self.ask(q).action, ANSWER, q)

    def test_hinglish_product_words(self):
        r = self.ask("chappal dikhao")
        self.assertEqual(r.action, ANSWER)
        self.assertTrue(all("Sandals" in p.title for p in self.cited_products(r)))

    def test_gender_words_filter_and_include_unisex(self):
        r = self.ask("ladies kurta")
        self.assertEqual(r.action, ANSWER)
        for p in self.cited_products(r):
            self.assertIn(p.attributes.get("gender"), ("women", "unisex"))

    def test_colour_alone_does_not_license_unknown_product(self):
        for q in ("red saree", "women's saree"):
            self.assertEqual(self.ask(q).action, REFUSE, q)

    def test_external_brand_is_refused_with_cited_alternatives(self):
        r = self.ask("nike running shoes")
        self.assertEqual(r.action, REFUSE)
        self.assertTrue(r.text.startswith("I don't have Nike"))
        self.assertTrue(self.cited_products(r))

    def test_external_brands_ending_in_s_survive_stemming(self):
        for q in ("adidas hoodie", "levis jeans", "skechers running shoes"):
            r = self.ask(q)
            self.assertEqual(r.action, REFUSE, q)
            self.assertIn(q.split()[0].capitalize(), r.text)  # shown unstemmed: "Adidas", not "Adida"

    def test_unsold_product_offers_closest_alternative(self):
        r = self.ask("headphones")
        self.assertEqual(r.action, REFUSE)
        self.assertTrue(all("Earbuds" in p.title for p in self.cited_products(r)))

    def test_question_about_feature_product_lacks(self):
        kettle = next(p for p in self.products.values() if "Kettle" in p.title)
        r = self.ask(f"does {kettle.title} have bluetooth")
        self.assertEqual(r.action, REFUSE)

    def test_stock_question_about_the_product_clarifies(self):
        self.assertEqual(self.ask("is the white backpack in stock").action, CLARIFY)

    def test_function_words_are_not_stemmed(self):
        self.assertIn("does", tokenize("how long does it take"))


if __name__ == "__main__":
    unittest.main()
