"""A search that failed only on its budget is remembered, but not for a message with a word we can't place."""

import unittest
from pathlib import Path

from api.state import session_from_dict, session_to_dict
from groundtruth.answering import REFUSE, Session
from groundtruth.builder import build_assistant
from groundtruth.config import get_settings

DATA = Path(__file__).resolve().parent.parent / "data"


class BudgetMemoryUnplacedWordTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.assistant = build_assistant(DATA / "catalogue.jsonl", get_settings().paths.data_dir / "policy.txt")

    def chat(self, *messages: str):
        session, replies = Session(), []
        for message in messages:
            replies.append(self.assistant.ask(message, session))
        return session, replies

    def test_an_unplaced_word_after_a_budget_miss_is_not_answered_with_the_old_product(self) -> None:
        _, (miss, next_reply) = self.chat("running shoes under 100", "hudieee under 1500")
        self.assertEqual(miss.action, REFUSE)
        self.assertEqual(next_reply.action, REFUSE)
        self.assertNotIn("Shoes", next_reply.text)
        self.assertIn("hudieee", next_reply.text)

    def test_a_budget_only_follow_up_still_uses_the_kept_search(self) -> None:
        session, (miss, follow) = self.chat("running shoes under 100", "under 5000")
        self.assertEqual(miss.action, REFUSE)
        self.assertIn("Shoes", follow.text)
        self.assertFalse(session.budget_kept)

    def test_the_flag_survives_storage_and_clears_on_reset(self) -> None:
        session, _ = self.chat("running shoes under 100")
        self.assertTrue(session.budget_kept)
        self.assertTrue(session_from_dict(session_to_dict(session)).budget_kept)
        session.clear()
        self.assertFalse(session.budget_kept)


if __name__ == "__main__":
    unittest.main()
