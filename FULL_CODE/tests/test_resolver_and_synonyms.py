"""The low-token resolver and the learned-synonym table: bounded prompts, validated picks, no unreviewed learning."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from groundtruth.answering import ShoppingAssistant
from groundtruth.catalogue import CatalogueIndex, load_catalogue
from groundtruth.config import get_settings
from groundtruth.models.calls import ModelCallError
from groundtruth.models.resolver import MENU_SIZE, SYSTEM, ShortlistNormaliser, TypeMenus, trigrams
from groundtruth.policy import load_policy
from groundtruth.synonyms import APPROVED, PROPOSED, LearnedSynonyms
from groundtruth.text import approx_tokens
from groundtruth.understanding import understand

DATA = Path(__file__).resolve().parent.parent / "data"


class FakeModel:
    """Stands in for the instruct model: answers a numbered menu by choosing the option named in `answers`."""

    def __init__(self, answers: dict[str, str]) -> None:
        self.answers, self.prompts, self.max_tokens = answers, [], []

    def __call__(self, system: str, user: str, max_tokens: int | None = None, json_mode: bool = True):
        self.prompts.append(user)
        self.max_tokens.append(max_tokens)
        lines = user.splitlines()
        word = lines[0].removeprefix("WORD: ")
        wanted = self.answers.get(word)
        options = [line.split(". ", 1)[1] for line in lines[1:]]
        number = options.index(wanted) + 1 if wanted in options else 0
        return str(number), approx_tokens(system + user), 1


class ResolverTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = CatalogueIndex(load_catalogue(DATA / "catalogue.jsonl"))
        cls.policy = load_policy(get_settings().paths.data_dir / "policy.txt")
        cls.menus = TypeMenus(cls.index)

    def test_spelling_neighbours_come_first(self) -> None:
        self.assertEqual(self.menus.closest("earbuds", 1)[0][0], "earbud")
        self.assertGreater(trigrams("kurta") & trigrams("kurtha"), frozenset())

    def test_menus_are_bounded(self) -> None:
        self.assertLessEqual(len(self.menus.categories), MENU_SIZE)
        for category in self.menus.categories:
            self.assertLessEqual(len(self.menus.in_category(category, "anything")), MENU_SIZE)

    def test_a_pick_maps_the_word_and_costs_a_few_hundred_tokens_at_most(self) -> None:
        picks = iter(["Electronics", "earbud"])  # the category menu first, then that category's types
        seen: list[tuple[str, int | None]] = []

        def model(system: str, user: str, max_tokens: int | None = None, json_mode: bool = True):
            options = [line.split(". ", 1)[1] for line in user.splitlines()[1:]]
            seen.append((user, max_tokens))
            return str(options.index(next(picks)) + 1), approx_tokens(system + user), 1

        resolver = ShortlistNormaliser(model)
        parsed = understand("headphone under 2000", self.index)
        self.assertEqual(parsed.unknown, ["headphone"])
        n_in, n_out = resolver.apply(parsed, self.index)
        self.assertEqual(parsed.mapped, [("headphone", "earbud")])
        self.assertEqual((parsed.unknown, "earbud" in parsed.spec.type_tokens), ([], True))
        self.assertEqual(resolver.calls, 2)
        self.assertLess(n_in + n_out, 400)  # the schema prompt was about 3,800 tokens on the pack's catalogue
        self.assertTrue(all(m == 4 for _, m in seen))

    def test_a_pick_outside_the_menu_is_dropped(self) -> None:
        for reply in ("99", "banana", "", "0"):
            parsed = understand("headphone", self.index)
            resolver = ShortlistNormaliser(lambda s, u, max_tokens=None, json_mode=True, r=reply: (r, 50, 1))
            resolver.apply(parsed, self.index)
            self.assertEqual((parsed.mapped, parsed.unknown), ([], ["headphone"]), reply)

    def test_a_failing_model_leaves_the_parse_alone(self) -> None:
        def broken(system, user, max_tokens=None, json_mode=True):
            raise ModelCallError("down")

        parsed = understand("headphone", self.index)
        self.assertEqual(ShortlistNormaliser(broken).apply(parsed, self.index), (0, 0))
        self.assertEqual(parsed.unknown, ["headphone"])

    def test_brands_the_shop_lacks_are_never_sent_to_the_model(self) -> None:
        model = FakeModel({})
        parsed = understand("do you sell iphone", self.index)
        ShortlistNormaliser(model).apply(parsed, self.index)
        self.assertEqual(model.prompts, [])

    def test_prompt_size_does_not_grow_with_the_catalogue(self) -> None:
        base = approx_tokens(SYSTEM) + max(
            approx_tokens(p)
            for p in ["WORD: headphone\n" + "\n".join(f"{i}. category{i}" for i in range(1, MENU_SIZE + 1))]
        )
        self.assertLess(base, 200)

    def test_assistant_learns_a_proposal_that_only_a_person_can_make_live(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            table = LearnedSynonyms(Path(tmp) / "syn.json")
            seq = iter(["Electronics", "earbud"])
            resolver = ShortlistNormaliser(
                lambda s, u, max_tokens=None, json_mode=True: FakeModel({"headphone": next(seq)})(
                    s, u, max_tokens, json_mode
                )
            )
            assistant = ShoppingAssistant(self.index, self.policy, normaliser=resolver, synonyms=table)
            first = assistant.ask("headphone under 2000")
            self.assertEqual(first.path, "llm")
            self.assertGreater(first.llm_input_tokens, 0)
            self.assertEqual(table.counts(), {PROPOSED: 1, APPROVED: 0, "rejected": 0})
            self.assertIsNone(table.lookup("headphone"))  # proposed, so not used yet
            # a person approves; the next ask needs no model at all
            table.approve("headphone")
            calls = resolver.calls
            second = assistant.ask("headphone under 2000")
            self.assertEqual((second.path, second.llm_input_tokens, resolver.calls), ("synonym", 0, calls))
            self.assertEqual(second.text, first.text)
            # and it survives a restart
            self.assertEqual(LearnedSynonyms(Path(tmp) / "syn.json").lookup("headphone"), "earbud")


class SynonymTableTest(unittest.TestCase):
    def test_rejected_and_approved_entries_are_not_overwritten_by_a_model(self) -> None:
        t = LearnedSynonyms()
        t.propose("a-word", "shoe", "llm")
        t.approve("a-word")
        t.propose("a-word", "kurta", "llm")
        self.assertEqual(t.lookup("a-word"), "shoe")
        t.reject("a-word")
        self.assertIsNone(t.lookup("a-word"))

    def test_seen_counts_and_pending_order(self) -> None:
        t = LearnedSynonyms()
        for _ in range(3):
            t.propose("jogger", "shoe", "llm")
        t.propose("slipper", "sandal", "llm")
        self.assertEqual([w for w, _ in t.pending()], ["jogger", "slipper"])

    def test_multiword_and_identity_proposals_are_ignored(self) -> None:
        t = LearnedSynonyms()
        t.propose("running shoe", "shoe", "llm")
        t.propose("shoe", "shoe", "llm")
        self.assertEqual(t.pending(), [])

    def test_a_broken_file_is_tolerated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "syn.json"
            path.write_text("{not json", encoding="utf-8")
            self.assertEqual(LearnedSynonyms(path).counts(), {PROPOSED: 0, APPROVED: 0, "rejected": 0})
            path.write_text(json.dumps({"x": {"target": "shoe", "status": "approved"}, "y": 5}), encoding="utf-8")
            self.assertEqual(LearnedSynonyms(path).lookup("x"), "shoe")

    def test_a_target_the_catalogue_no_longer_has_is_ignored(self) -> None:
        index = CatalogueIndex(load_catalogue(DATA / "catalogue.jsonl"))
        t = LearnedSynonyms()
        t.approve("headphone", "zeppelin")
        parsed = understand("headphone", index)
        self.assertFalse(t.apply(parsed, index))


if __name__ == "__main__":
    unittest.main()
