"""Regression tests for vocabulary and routing fixes found with independently written queries.

Run: python -m unittest discover -s tests
"""
import unittest

from groundtruth.paths import ROOT
from groundtruth.grounding import Grounding
from groundtruth import ANSWER, CLARIFY, REFUSE, Session, build_assistant
from groundtruth.text import tokenize


class VocabularyFixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a = build_assistant(ROOT / "data" / "catalogue.jsonl", ROOT / "data" / "policy.txt")
        cls.g = Grounding(cls.a.index, cls.a.sections)
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

    def test_brand_names_keep_their_own_spelling(self):
        self.assertTrue(self.ask("do you sell iPhones?").text.startswith("I don't have iPhone"))

    def test_follow_up_naming_something_unsold_is_refused_not_relisted(self):
        r = self.ask("black hoodie", "laptop")
        self.assertEqual(r.action, REFUSE)  # never a fresh listing presented as an answer
        s = Session()
        for t in ("black hoodie", "laptop", "under 3000"):  # the earlier search is kept, not polluted
            r = self.a.ask(t, s)
        self.assertEqual(r.action, ANSWER)
        self.assertTrue(all("Hoodie" in p.title and p.price <= 3000 for p in self.cited_products(r)))

    def test_compare_by_product_ids(self):
        ids = sorted(self.products)[:3]
        r = self.ask(f"compare {ids[0]} and {ids[1]} vs {ids[2]}")
        self.assertEqual(r.action, ANSWER)
        self.assertEqual(set(r.citations), set(ids))

    def test_single_product_id_still_answers_about_that_product(self):
        pid = sorted(self.products)[0]
        r = self.ask(f"{pid} price")
        self.assertEqual((r.action, r.citations), (ANSWER, [pid]))

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
