"""Short misspellings ("jeens"), "did you mean" offers, and a budget that found nothing staying in force.

The catalogue is written here so the tests do not depend on the generated data.

Run: python -m unittest discover -s tests
"""

import json
import tempfile
import unittest
from pathlib import Path

from groundtruth.answering import ANSWER, REFUSE, Session
from groundtruth.builder import build_assistant
from groundtruth.catalogue import Product
from groundtruth.grounding import Grounding
from groundtruth.paths import ROOT


def _product(pid, title, category, price, **attributes):
    return {
        "id": pid,
        "title": title,
        "description": f"{title}.",
        "category": category,
        "attributes": attributes,
        "price": price,
        "in_stock": True,
        "rating": 4.2,
        "rating_count": 100,
    }


PRODUCTS = [
    _product("P1", "Denimo Slim Jeans (Blue)", "Apparel", 1199, brand="Denimo", color="blue", material="denim"),
    _product("P2", "Denimo Relaxed Jeans (Black)", "Apparel", 1399, brand="Denimo", color="black", material="denim"),
    _product("P3", "Threadco Basic Shirt (White)", "Apparel", 499, brand="Threadco", color="white", material="cotton"),
    _product(
        "P4",
        "Roshni Cotton Kurta (Green)",
        "Apparel",
        929,
        brand="Roshni",
        color="green",
        material="cotton",
        gender="women",
    ),
    _product(
        "P5",
        "Roshni Festive Kurta (Red)",
        "Apparel",
        989,
        brand="Roshni",
        color="red",
        material="cotton",
        gender="women",
    ),
    _product("P6", "Volta Slim Power Bank (Grey)", "Electronics", 839, brand="Volta", color="grey"),
    _product("P7", "Casa Nova Bedsheet (Blue)", "Home", 2379, brand="Casa", color="blue"),
    _product("P8", "Warmo Zip Hoodie (Black)", "Apparel", 1500, brand="Warmo", color="black"),
    _product("P9", "Steel Hammer (Grey)", "Tools", 350, brand="Forge", color="grey"),
    _product("P10", "Steel Hummer (Grey)", "Tools", 360, brand="Forge", color="grey"),
]


class TypoAndBudgetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        path = Path(cls.tmp.name) / "catalogue.jsonl"
        path.write_text("\n".join(json.dumps(p) for p in PRODUCTS), encoding="utf-8")
        cls.a = build_assistant(path, ROOT / "data" / "policy.txt")
        cls.g = Grounding(cls.a.index, cls.a.sections)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def ask(self, *turns):
        s = Session()
        for t in turns:
            r = self.a.ask(t, s)
        self.assertFalse(self.g.check(r.text, " ".join(turns))["problems"], r.text)  # every reply stays grounded
        return r

    # -- short misspellings ----------------------------------------------------------------------
    def test_jeens_and_jens_are_read_as_jeans_and_say_so(self):
        for typed in ("jeens", "jens"):
            r = self.ask(f"{typed} under 1500")
            self.assertEqual(r.action, ANSWER, typed)
            self.assertIn(f'(you typed "{typed}")', r.text)
            self.assertEqual(set(r.citations), {"P1", "P2"}, typed)

    def test_the_budget_still_applies_to_a_mended_word(self):
        r = self.ask("jeens under 1300")
        self.assertEqual(r.citations, ["P1"])
        r = self.ask("jeens under 700")
        self.assertEqual(r.action, REFUSE)
        self.assertIn("₹700", r.text)
        self.assertIn("[P1]", r.text)  # the nearest real option is cited, not an invented one

    def test_a_vowel_slip_in_a_longer_word_is_mended(self):
        r = self.ask("shert white")
        self.assertEqual(r.action, ANSWER)
        self.assertEqual(r.citations, ["P3"])

    def test_a_changed_consonant_is_not_mended(self):
        # "band" is a word of its own, not a slip for the "bank" of "power bank"
        r = self.ask("need a fitness band")
        self.assertEqual(r.action, REFUSE)
        self.assertNotIn("P6", r.citations)
        self.assertNotIn("Showing results", r.text)

    def test_a_word_is_never_mended_into_a_brand(self):
        # "cases" is one vowel away from the brand Casa
        r = self.ask("phone cases")
        self.assertNotIn("P7", r.citations)
        self.assertNotIn("Showing results", r.text)

    def test_two_possible_words_mean_no_guess(self):
        index = self.a.index
        self.assertEqual(index.closest_short("hammer"), None)  # an exact word is not a typo
        self.assertIsNone(index.closest_short("hemmer"))  # one slip from both "hammer" and "hummer"
        self.assertEqual(index.closest_short("jeen"), "jean")

    def test_words_that_are_too_short_or_not_letters_are_left_alone(self):
        for word in ("ab", "j3ns", "j-ns", ""):
            self.assertIsNone(self.a.index.closest_short(word), word)

    def test_a_product_added_live_can_be_found_through_a_slip(self):
        a = build_assistant(Path(self.tmp.name) / "catalogue.jsonl", ROOT / "data" / "policy.txt")
        self.assertIsNone(a.index.closest_short("jecket"))  # nothing sells jackets yet
        a.index.add_product(Product("P11", "Warmo Rain Jacket (Navy)", "Rain jacket.", "Apparel", 1799, True))
        a.index.refresh()  # the batch of live updates is done: vocabularies and typo tables are rebuilt
        self.assertEqual(a.index.closest_short("jecket"), "jacket")
        r = a.ask("jecket under 2000", Session())
        self.assertEqual(r.action, ANSWER)
        self.assertIn("P11", r.citations)

    # -- "did you mean" --------------------------------------------------------------------------
    def test_a_near_miss_gets_a_question_not_an_answer(self):
        r = self.ask("hoddie")
        self.assertEqual(r.action, REFUSE)
        self.assertIn('Did you mean "Hoodie"?', r.text)
        self.assertEqual(r.citations, [])  # asking cites nothing: it does not search for the guess

    def test_nothing_close_gets_no_offer(self):
        r = self.ask("laptpo")
        self.assertEqual(r.action, REFUSE)
        self.assertNotIn("Did you mean", r.text)

    def test_an_offer_is_never_a_colour_brand_or_gender_word(self):
        for word in ("blu", "reed", "wimen"):
            offers = self.a.index.suggest(word)
            self.assertFalse({"Blue", "Red", "Women", "Casa"} & set(offers), (word, offers))

    def test_an_offer_keeps_the_first_letter_and_the_length(self):
        # On the real pack "saal" was offered "Sandals", "chawal" "Hawa" and "store" "Storage": a loose match is a guess
        index = self.a.index
        words = sorted({w for n in index._offers_by_len for w in index._offers_by_len[n]})
        offers = 0
        for word in words:
            for typed in (word[:-1], word[:-2], word[:2] + word[3:], word + word[-1], "x" + word[1:]):
                for offer in index.suggest(typed):
                    offers += 1
                    # the matched word may be a synonym ("battalu" shows as "Apparel"): one of them must fit
                    matched = [w for w, kind in index._offers.items() if index.display_word(kind) == offer]
                    self.assertTrue(
                        any(w[0] == typed[0] and abs(len(w) - len(typed)) <= 1 for w in matched),
                        (typed, offer, matched),
                    )
        self.assertGreater(offers, 0)

    def test_a_word_cut_short_is_not_offered_the_longer_product(self):
        self.assertEqual(self.a.index.suggest("jeanz"), ["Jeans"])  # a real slip: same first letter, same length
        self.assertEqual(self.a.index.suggest("jea"), [])  # too short to be a typo
        self.assertEqual(self.a.index.suggest("jeansxx"), [])  # two letters longer: a different word, not a slip

    # -- a budget that found nothing stays in force ----------------------------------------------
    def test_budget_is_remembered_after_nothing_fitted_it(self):
        s = Session()
        self.a.ask("cotton kurti for women", s)
        first = self.a.ask("500 ke andar", s)
        self.assertEqual(first.action, REFUSE)
        second = self.a.ask("sabse sasti", s)  # the shopper's budget did not go away
        self.assertEqual(second.action, REFUSE)
        self.assertIn("under ₹500", second.text)
        chat = "cotton kurti for women 500 ke andar sabse sasti"  # the budget is the shopper's own number
        self.assertEqual(self.g.check(second.text, chat)["problems"], [])

    def test_a_new_budget_replaces_the_one_that_failed(self):
        s = Session()
        for turn in ("cotton kurti for women", "500 ke andar", "sabse sasti"):
            self.a.ask(turn, s)
        r = self.a.ask("1500 ke andar", s)
        self.assertEqual(r.action, ANSWER)
        self.assertEqual(r.citations[0], "P4")  # still cheapest first, within the new budget

    def test_a_failed_search_that_is_not_about_money_is_not_remembered(self):
        s = Session()
        self.a.ask("black hoodie", s)
        self.a.ask("laptop", s)  # not sold: refused, and the hoodie search must survive
        self.assertEqual(s.spec.type_tokens, ["hoodie"])
        self.assertIsNone(s.spec.price_max)


if __name__ == "__main__":
    unittest.main()
