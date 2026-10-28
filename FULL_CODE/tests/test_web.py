"""The web UI's server: every reply comes back grounding-checked, with its sources, and bad input is handled.

Run: python -m unittest discover -s tests
"""

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from api.server import make_handler
from api.state import State
from groundtruth.builder import build_assistant
from groundtruth.paths import ROOT


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assistant = cls.assistant = build_assistant(ROOT / "data" / "catalogue.jsonl", ROOT / "data" / "policy.txt")
        page = (ROOT / "web" / "index.html").read_bytes()
        insights = (ROOT / "web" / "insights.html").read_bytes()
        cls.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), make_handler(State(assistant), page, {"products": 1}, insights)
        )
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def post(self, path, body, raw=None):
        data = raw if raw is not None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data, {"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as r:
            return r.read().decode()

    def test_pages_are_served_and_self_contained(self):
        for path in ("/", "/insights"):
            html = self.get(path)
            self.assertIn("GroundTruth", html)
            for external in ("<script src=", '<link rel="stylesheet"', "fonts.googleapis", "cdn."):
                self.assertNotIn(external, html)  # must work offline

    def test_compare_by_ids_returns_all_records(self):
        _, d = self.post("/api/chat", {"message": "running shoes"})
        ids = [c for c in d["sources"] if not c.startswith("policy:")][:2]
        _, d = self.post("/api/chat", {"message": f"compare {ids[0]} and {ids[1]} price rating stock"})
        self.assertEqual(d["action"], "answer")
        self.assertTrue(d["text"].startswith("Here is the comparison"))
        self.assertEqual(set(ids), set(d["sources"]))
        self.assertTrue(d["verified"]["ok"])

    def test_insights_count_without_keeping_messages(self):
        secret = "do you sell zorblaxian moonboots for my cousin priya"
        self.post("/api/chat", {"message": secret})
        self.post("/api/chat", {"message": "price of the blue running shoe"})
        d = json.loads(self.get("/api/insights"))
        self.assertGreaterEqual(d["actions"]["refuse"], 1)
        self.assertGreaterEqual(d["actions"]["clarify"], 1)
        self.assertEqual(d["failed_checks"], 0)
        dump = json.dumps(d) + self.get("/api/insights.csv")
        self.assertNotIn(secret, dump)  # the full message is never stored
        self.assertNotIn("session", dump)
        self.assertIn("requested_item,times_refused", self.get("/api/insights.csv"))

    def test_reply_is_checked_and_sources_match_citations(self):
        code, d = self.post("/api/chat", {"message": "running shoes under 3000"})
        self.assertEqual(code, 200)
        self.assertEqual(d["action"], "answer")
        self.assertTrue(d["verified"]["ok"], d["verified"])
        cited = set(__import__("re").findall(r"\[([^\]]+)\]", d["text"]))
        self.assertEqual(cited, set(d["sources"]))
        for src in d["sources"].values():
            self.assertTrue(src["price"].startswith("₹"))

    def test_session_remembers_follow_ups_and_resets(self):
        _, d = self.post("/api/chat", {"message": "running shoes"})
        sid = d["session_id"]
        _, d = self.post("/api/chat", {"session_id": sid, "message": "under 2500"})
        self.assertEqual(d["remembered"]["budget_max"], 2500)
        self.assertIn("running", d["remembered"]["looking_for"])
        _, d = self.post("/api/reset", {"session_id": sid})
        self.assertIsNone(d["remembered"]["budget_max"])

    def test_policy_source_and_refusal_gap(self):
        _, d = self.post("/api/chat", {"message": "refund kab milega"})
        self.assertIn("policy:returns", d["sources"])
        _, d = self.post("/api/chat", {"session_id": d["session_id"], "message": "do you sell iPhones?"})
        self.assertEqual(d["action"], "refuse")
        self.assertIn("iphone", [w for w, _ in d["gaps"]])

    def test_examples_come_from_the_catalogue_and_do_what_they_say(self):
        from api.views import build_examples
        from groundtruth.answering import Session

        examples = build_examples(self.assistant)
        self.assertGreaterEqual(len(examples), 5)
        self.assertEqual({e["shows"] for e in examples}, {"answer", "clarify", "refuse"})
        for e in examples:
            self.assertEqual(self.assistant.ask(e["q"], Session()).action, e["shows"], e)

    def test_budget_from_an_earlier_turn_is_not_flagged(self):
        _, d = self.post("/api/chat", {"message": "running shoes"})
        _, d = self.post("/api/chat", {"session_id": d["session_id"], "message": "under 200"})
        self.assertEqual(d["action"], "refuse")
        self.assertTrue(d["verified"]["ok"], d["verified"])
        self.assertIn("₹200", d["text"])

    def test_demand_label_does_not_carry_old_filters_into_a_new_request(self):
        _, d = self.post("/api/chat", {"message": "blue running shoes"})
        _, d = self.post("/api/chat", {"session_id": d["session_id"], "message": "kurta under 100"})
        self.assertEqual(d["action"], "refuse")
        self.assertIn(["kurta, under ₹100", 1], d["gaps"])

    def test_picking_a_card_after_a_clarifying_question_answers_it(self):
        _, d = self.post("/api/chat", {"message": "price of the blue running shoe"})
        self.assertEqual(d["action"], "clarify")
        pick = next(v for v in d["sources"].values() if v["kind"] == "product")
        self.assertTrue(pick["noun"])  # cards show what the product is called
        _, d = self.post("/api/chat", {"session_id": d["session_id"], "message": pick["title"]})
        self.assertEqual(d["action"], "answer")
        self.assertIn(pick["price"], d["text"])
        self.assertEqual(list(d["sources"]), [pick["id"]])

    def test_order_questions_are_not_counted_as_missing_products(self):
        _, d = self.post("/api/chat", {"message": "track my order"})
        self.assertEqual(d["action"], "refuse")
        self.assertEqual(d["gaps"], [])

    def test_bad_input(self):
        self.assertEqual(self.post("/api/chat", {"message": "   "})[0], 400)
        self.assertEqual(self.post("/api/chat", None, raw=b"not json")[0], 400)
        self.assertEqual(self.post("/api/nope", {})[0], 404)
        code, d = self.post("/api/chat", {"message": "x" * 5000})  # rejected with the limit, not silently cut
        self.assertEqual(code, 413)
        self.assertIn("500 characters", d["error"])


if __name__ == "__main__":
    unittest.main()
