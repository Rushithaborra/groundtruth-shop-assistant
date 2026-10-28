"""The web API refuses bad requests, sends security headers, blocks other sites, and limits request rates."""

import json
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from api.ratelimit import RateLimiter
from api.security import content_security_policy, same_origin
from api.server import make_handler
from api.state import State
from api.validation import RequestError, chat_request, read_json_body
from groundtruth.builder import build_assistant
from groundtruth.paths import ROOT


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class ValidationTests(unittest.TestCase):
    def body(self, data, ctype="application/json", length=None):
        raw = json.dumps(data).encode() if not isinstance(data, bytes) else data
        return read_json_body(ctype, str(len(raw) if length is None else length), raw, 1000)

    def status(self, fn) -> int:
        with self.assertRaises(RequestError) as ctx:
            fn()
        return ctx.exception.status

    def test_body_type_size_and_shape(self):
        self.assertEqual(self.body({"message": "hi"}), {"message": "hi"})
        self.assertEqual(self.status(lambda: self.body({"m": 1}, ctype="text/plain")), 415)
        self.assertEqual(self.status(lambda: self.body({"m": 1}, length="")), 411)
        self.assertEqual(self.status(lambda: self.body(b"x" * 2000)), 413)
        self.assertEqual(self.status(lambda: self.body(b"not json")), 400)
        self.assertEqual(self.status(lambda: self.body([1, 2])), 400)

    def test_chat_fields(self):
        self.assertEqual(chat_request({"message": " hi ", "session_id": None}, 50).message, "hi")
        bad = [
            ({"message": 5}, 400), ({"message": "   "}, 400), ({"message": "x" * 51}, 413),
            ({"message": "hi\x00"}, 400), ({"message": "hi", "admin": True}, 400),
            ({"message": "hi", "session_id": "../etc"}, 400), ({"message": "hi", "session_id": 7}, 400),
        ]  # fmt: skip
        for data, code in bad:
            with self.subTest(data=data):
                self.assertEqual(self.status(lambda d=data: chat_request(d, 50)), code)


class RateLimiterTests(unittest.TestCase):
    def test_limit_per_key_in_a_sliding_window(self):
        clock = Clock()
        limiter = RateLimiter(2, 60, clock=clock)
        self.assertEqual([limiter.check("a"), limiter.check("a")], [0.0, 0.0])
        self.assertAlmostEqual(limiter.check("a"), 60.0)
        self.assertEqual(limiter.check("b"), 0.0)  # keys are independent
        clock.now = 60.5
        self.assertEqual(limiter.check("a"), 0.0)  # the window has moved on

    def test_memory_is_bounded(self):
        limiter = RateLimiter(1, 60, max_keys=3, clock=Clock())
        for key in "abcdef":
            limiter.check(key)
        self.assertEqual(len(limiter._hits), 3)


class SecurityHelperTests(unittest.TestCase):
    def test_csp_allows_only_the_pages_own_scripts(self):
        csp = content_security_policy(b"<script>alert(1)</script><p>x</p>")
        self.assertIn("default-src 'none'", csp)
        self.assertIn("script-src 'sha256-", csp)
        self.assertNotIn("unsafe-inline' ;", csp.split("script-src")[1].split(";")[0])
        self.assertIn("frame-ancestors 'none'", csp)

    def test_same_origin(self):
        self.assertTrue(same_origin(None, "127.0.0.1:8000"))
        self.assertTrue(same_origin("http://127.0.0.1:8000", "127.0.0.1:8000"))
        self.assertFalse(same_origin("https://evil.example", "127.0.0.1:8000"))


class ServerSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assistant = build_assistant(ROOT / "data" / "catalogue.jsonl", ROOT / "data" / "policy.txt")
        page = (ROOT / "web" / "index.html").read_bytes()
        limiters = (RateLimiter(2, 60), RateLimiter(1000, 60))  # 2 messages a minute per session
        handler = make_handler(State(assistant), page, {"products": 1}, limiters=limiters)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def request(self, path, body=None, method=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data, {"Content-Type": "application/json", **(headers or {})},
                                     method=method)  # fmt: skip
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def test_every_response_carries_security_headers_and_pages_a_csp(self):
        code, headers, _ = self.request("/")
        self.assertEqual(code, 200)
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("script-src 'sha256-", headers["Content-Security-Policy"])
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(self.request("/api/nope")[1]["Referrer-Policy"], "no-referrer")

    def test_other_sites_and_other_methods_are_refused(self):
        evil = self.request("/api/chat", {"message": "hi"}, headers={"Origin": "https://evil.example"})
        self.assertEqual(evil[0], 403)
        self.assertEqual(self.request("/api/chat", method="DELETE")[0], 405)
        self.assertEqual(self.request("/api/chat", {"message": "hi"}, method="OPTIONS")[0], 405)

    def test_refusals_with_a_request_body_never_reset_the_connection(self):
        # A refusal sent before the body was read made Windows reset the connection on ~1-3% of requests, so the
        # client saw ConnectionAbortedError instead of the status. Repeat enough times to catch that reliably.
        refused = (
            ("/api/chat", "POST", {"Origin": "https://evil.example"}, 403),
            ("/api/nope", "POST", {}, 404),
            ("/api/chat", "OPTIONS", {}, 405),
        )
        for path, method, headers, expected in refused:
            for _ in range(100):
                self.assertEqual(self.request(path, {"message": "hi"}, method=method, headers=headers)[0], expected)

    def test_a_session_is_rate_limited_with_retry_after(self):
        code, _, body = self.request("/api/chat", {"message": "hi"})
        sid = json.loads(body)["session_id"]
        self.assertEqual(self.request("/api/chat", {"message": "hi", "session_id": sid})[0], 200)
        self.assertEqual(self.request("/api/chat", {"message": "hi", "session_id": sid})[0], 200)
        code, headers, _ = self.request("/api/chat", {"message": "hi", "session_id": sid})
        self.assertEqual(code, 429)
        self.assertGreaterEqual(int(headers["Retry-After"]), 1)

    def test_query_strings_are_ignored(self):
        self.assertEqual(self.request("/api/info?x=<script>")[0], 200)


if __name__ == "__main__":
    unittest.main()
