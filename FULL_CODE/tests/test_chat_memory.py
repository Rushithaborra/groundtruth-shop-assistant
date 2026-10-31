"""Web-chat memory: a new product keeps the budget, size and brand/colour/gender, earlier searches can be returned to,
and none of it happens for a plain Session (the scored path)."""

import unittest
from pathlib import Path

from api.state import State, session_from_dict, session_to_dict
from groundtruth.answering import ANSWER, Session
from groundtruth.builder import build_assistant
from groundtruth.config import get_settings
from groundtruth.storage import MemoryBackend

DATA = Path(__file__).resolve().parent.parent / "data"
KEPT = "(Kept from your earlier search:"


class ChatMemoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.assistant = build_assistant(DATA / "catalogue.jsonl", get_settings().paths.data_dir / "policy.txt")

    def chat(self, *messages: str, carry: bool = True):
        session, replies = Session(carry=carry), []
        for message in messages:
            replies.append(self.assistant.ask(message, session))
        return session, replies

    def prices(self, reply) -> list[float]:
        return [self.assistant.index.products[c].price for c in reply.citations if c in self.assistant.index.products]

    def test_a_plain_session_remembers_nothing_across_products(self) -> None:
        _, (_, hoodies) = self.chat("black jeans under 1500", "hoodie", carry=False)
        self.assertNotIn(KEPT, hoodies.text)

    def test_the_budget_and_colour_come_along_to_the_next_product(self) -> None:
        _, (jeans, hoodies) = self.chat("black jeans under 3000", "hoodie")
        self.assertEqual(jeans.action, ANSWER)
        self.assertEqual(hoodies.action, ANSWER)
        self.assertTrue(hoodies.text.startswith(KEPT))
        self.assertIn("under 3,000 rupees", hoodies.text.split("\n")[0])
        self.assertTrue(self.prices(hoodies))
        self.assertTrue(all(price <= 3000 for price in self.prices(hoodies)))

    def test_what_a_new_message_says_itself_wins(self) -> None:
        _, (_, hoodies) = self.chat("jeans under 3000", "hoodie under 1200")
        self.assertTrue(all(price <= 1200 for price in self.prices(hoodies)))
        self.assertNotIn("under 3,000 rupees", hoodies.text.split("\n")[0])

    def test_a_carried_item_that_leaves_nothing_is_dropped_and_said(self) -> None:
        _, (_, hoodies) = self.chat("jeans under 100", "hoodie")
        self.assertEqual(hoodies.action, ANSWER)
        self.assertIn("started fresh", hoodies.text.split("\n")[0])

    def test_back_to_an_earlier_product(self) -> None:
        session, (_, _, back) = self.chat("jeans under 3000", "hoodie", "back to the jeans")
        index = self.assistant.index.products
        cited = [index[c] for c in back.citations if c in index]
        self.assertTrue(cited and all("jean" in p.title.lower() for p in cited))
        self.assertNotIn("can't verify", back.text)
        self.assertEqual([e["label"].lower() for e in session.history], ["hoodie"])

    def test_back_to_with_a_new_budget(self) -> None:
        _, (_, _, back) = self.chat("jeans", "hoodie", "back to jeans under 2000")
        self.assertTrue(self.prices(back) and all(price <= 2000 for price in self.prices(back)))

    def test_narrowing_the_same_product_does_not_file_it_as_earlier(self) -> None:
        session, _ = self.chat("jeans under 3000", "black jeans")
        self.assertEqual(session.history, [])

    def test_the_history_is_stored_with_the_chat_but_the_switch_is_not(self) -> None:
        session, _ = self.chat("jeans under 3000", "hoodie")
        again = session_from_dict(session_to_dict(session))
        self.assertEqual(again.history, session.history)
        self.assertFalse(again.carry)
        self.assertTrue(session_from_dict(session_to_dict(session), carry=True).carry)

    def test_the_web_chat_has_memory_and_lists_earlier_searches(self) -> None:
        state = State(self.assistant, MemoryBackend(10, 600))
        first = state.chat(None, "black jeans under 3000")
        self.assertEqual(first["earlier"], [])
        second = state.chat(first["session_id"], "hoodie")
        self.assertTrue(second["text"].startswith(KEPT))
        self.assertTrue(second["verified"]["ok"], second["verified"]["problems"])  # the note passes the grounding check
        self.assertEqual([e["label"].lower() for e in second["earlier"]], ["jeans"])
        third = state.chat(first["session_id"], "back to jeans")
        self.assertEqual([e["label"].lower() for e in third["earlier"]], ["hoodie"])
        self.assertEqual(state.reset(first["session_id"])["earlier"], [])


if __name__ == "__main__":
    unittest.main()
