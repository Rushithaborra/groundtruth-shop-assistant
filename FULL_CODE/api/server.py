"""Local web UI for the shopping assistant. Standard library only, fully offline.

    python main.py serve                       # http://127.0.0.1:8000
    python main.py serve --catalogue data/scale/catalogue.jsonl --policy data/scale/policy.txt --port 8080
    python main.py serve --embed-model PATH --llm-model PATH   # optional fallbacks, same as chat

Every reply is re-checked by the same independent grounding checker the evaluator uses, and the result is shown next
to the reply. Citation chips open the exact catalogue record or policy section the claim came from.

/insights is a retailer view across all chats since the server started: how questions were answered, what was asked
for that nothing in the catalogue fits, words it couldn't place (a typo or synonym may be stocked), and which
questions needed clarifying. It keeps counts and short labels only, in memory; no full conversations, no session
ids, nothing written to disk.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from groundtruth.arguments import add_model_arguments
from groundtruth.builder import build_assistant
from groundtruth.config import ServerSettings, get_settings
from groundtruth.paths import ROOT, WEB_DIR

from .ratelimit import RateLimiter
from .security import SECURITY_HEADERS, content_security_policy, same_origin
from .state import State
from .validation import ChatRequest, RequestError, chat_request, read_json_body, reset_request
from .views import build_examples

logger = logging.getLogger(__name__)


def pick_data(catalogue: str | None, policy: str | None) -> tuple[str, str]:
    """Use what was passed; else the problem pack's catalogue if it is in data/pack/; else the synthetic 5,000-product
    demo catalogue, generated on first run."""
    paths = get_settings().paths
    if catalogue:
        return catalogue, policy or str(paths.policy)
    if paths.pack_catalogue.exists():
        return str(paths.pack_catalogue), policy or str(paths.pack_policy)
    if not paths.demo_catalogue.exists():
        logger.info("Generating the 5,000-product demo catalogue (first run only)...")
        subprocess.run([sys.executable, "-m", "tools.generate_scale"], check=True, cwd=ROOT, stdout=subprocess.DEVNULL)
    return str(paths.demo_catalogue), policy or str(paths.demo_policy)


class Handler(BaseHTTPRequestHandler):
    """Answers one request. `make_handler` returns a subclass bound to the shared state, pages and rate limits."""

    server_version = "GroundTruth/1"
    sys_version = ""  # don't advertise the Python version
    # bound by make_handler
    state: State
    info: dict
    pages: dict[str, bytes]
    policies: dict[str, str]
    limits: ServerSettings
    per_session: RateLimiter
    per_address: RateLimiter

    def log_message(self, fmt: str, *args: object) -> None:  # access log only at debug level
        """Route the standard library's access log to our logger, at debug level only."""
        logger.debug("%s " + fmt, self.address_string(), *args)

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None) -> None:
        """Send one response with its status, type, length and the security headers."""
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for name, value in {**SECURITY_HEADERS, **(extra or {})}.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, data: dict, extra: dict | None = None) -> None:
        """Send a JSON response (UTF-8, non-ASCII kept as is)."""
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8", extra)

    def _route(self) -> str:
        """The request path without its query string."""
        return urlsplit(self.path).path  # query strings are ignored, never parsed

    def do_GET(self) -> None:
        """Serve the two pages, /api/info, and the retailer view's JSON and CSV; anything else is 404."""
        path = self._route()
        if path in self.pages:
            csp = {"Content-Security-Policy": self.policies[path]}
            self._send(200, self.pages[path], "text/html; charset=utf-8", csp)
        elif path == "/api/info":
            self._json(200, self.info)
        elif path == "/api/insights":
            self._json(200, self.state.insights.summary())
        elif path == "/api/insights.csv":
            self._send(200, self.state.insights.gaps_csv().encode("utf-8"), "text/csv; charset=utf-8")
        else:
            self._json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        """Handle /api/chat and /api/reset: same-site only, validated JSON, rate-limited; errors as JSON."""
        declared, body = self._read_body()  # always consume the body, even when about to refuse (see _read_body)
        path = self._route()
        if path not in ("/api/chat", "/api/reset"):
            return self._json(404, {"error": "Not found"})
        if not same_origin(self.headers.get("Origin"), self.headers.get("Host")):
            return self._json(403, {"error": "Requests from other sites are not accepted"})
        try:
            data = read_json_body(self.headers.get("Content-Type"), declared, body, self.limits.max_body_bytes)
            if path == "/api/reset":
                return self._json(200, self.state.reset(reset_request(data)))
            request = chat_request(data, self.limits.max_message_chars)
            wait = self._seconds_to_wait(request)
            if wait:
                retry = {"Retry-After": RateLimiter.retry_after_header(wait)}
                return self._json(429, {"error": "Too many questions at once; please wait a moment"}, retry)
        except RequestError as exc:
            return self._json(exc.status, {"error": str(exc)})
        self._chat(request)

    def _seconds_to_wait(self, request: ChatRequest) -> float:
        """How long the shopper must wait before this request is allowed (0 when it is allowed now)."""
        wait = self.per_address.check(self.client_address[0])
        if request.session_id:  # a first message has no session yet: only the address limit applies
            wait = max(wait, self.per_session.check(request.session_id))
        return wait

    def _chat(self, request: ChatRequest) -> None:
        """Answer one chat message; an unexpected error becomes a 500 reply instead of stopping the server."""
        try:
            self._json(200, self.state.chat(request.session_id, request.message))
        except Exception:  # boundary: one failing query must not stop the server; the shopper's text isn't logged
            logger.exception("assistant error on a %d-character message", len(request.message))
            self._json(500, {"error": "The assistant hit an error on that message"})

    def _read_body(self) -> tuple[str | None, bytes]:
        """Read the request body (never more than the cap). It is read before every reply, including early
        refusals: closing a socket that still holds unread request data makes Windows reset the connection, and
        the client then loses the reply (ConnectionAbortedError) instead of seeing the 403/404/405."""
        declared = self.headers.get("Content-Length")
        try:
            to_read = min(max(int(declared or 0), 0), self.limits.max_body_bytes + 1)
        except ValueError:
            to_read = 0
        return declared, (self.rfile.read(to_read) if to_read else b"")

    def _not_allowed(self) -> None:
        """Refuse any method other than GET and POST with 405 (after reading the body; see _read_body)."""
        self._read_body()
        self._json(405, {"error": "Method not allowed"}, {"Allow": "GET, POST"})

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = _not_allowed


def make_handler(
    state: State,
    page: bytes,
    info: dict,
    insights_page: bytes = b"",
    limiters: tuple[RateLimiter, RateLimiter] | None = None,
) -> type[Handler]:
    """The request handler class; `limiters` (per session, per address) default to the configured rate limits."""
    limits = get_settings().server
    per_session, per_address = limiters or (
        RateLimiter(limits.rate_limit_per_minute, 60),
        RateLimiter(limits.rate_limit_per_address_per_minute, 60),
    )
    pages = {"/": page, "/index.html": page, **({"/insights": insights_page} if insights_page else {})}
    bound = {
        "state": state,
        "info": info,
        "pages": pages,
        "policies": {path: content_security_policy(body) for path, body in pages.items()},
        "limits": limits,
        "per_session": per_session,
        "per_address": per_address,
    }
    return type("BoundHandler", (Handler,), bound)


def add_arguments(ap: argparse.ArgumentParser) -> None:
    """The serve command's options: data files, models, host, port and the price snapshot date."""
    ap.add_argument("--catalogue", help="catalogue file (default: the pack's, else the synthetic 5,000-product demo)")
    ap.add_argument("--policy", help="policy file (default: the one that goes with the catalogue)")
    server = get_settings().server
    add_model_arguments(ap)
    ap.add_argument("--host", default=server.host)
    ap.add_argument("--port", type=int, default=server.port)
    ap.add_argument(
        "--snapshot",
        help="date the catalogue prices were taken, e.g. 2026-10-01 (default: the catalogue file's last-modified date)",
    )


def run(args: argparse.Namespace) -> None:
    """Build the assistant once and serve the demo, the retailer view and the JSON API until stopped."""
    args.catalogue, args.policy = pick_data(args.catalogue, args.policy)
    assistant = build_assistant(args.catalogue, args.policy, embed_model=args.embed_model, llm_model=args.llm_model)
    snapshot, source = args.snapshot, "given"
    if not snapshot:
        snapshot, source = datetime.fromtimestamp(Path(args.catalogue).stat().st_mtime).date().isoformat(), "file date"
    info = {
        "products": len(assistant.index.products),
        "policy_sections": len(assistant.sections),
        "semantic": assistant.semantic is not None,
        "llm": assistant.normaliser is not None,
        "snapshot": snapshot,
        "snapshot_source": source,
        "examples": build_examples(assistant),
    }
    page = (WEB_DIR / "index.html").read_bytes()
    insights_page = (WEB_DIR / "insights.html").read_bytes()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(State(assistant), page, info, insights_page))
    logger.info(
        "GroundTruth is running at http://%s:%d (%d products, prices as of %s). "
        "Retailer view: http://%s:%d/insights  Ctrl+C to stop.",
        args.host,
        args.port,
        info["products"],
        snapshot,
        args.host,
        args.port,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopped.")
