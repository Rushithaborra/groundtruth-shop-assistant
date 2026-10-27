"""Local web UI for the shopping assistant. Standard library only, fully offline.

    python main.py serve                       # http://127.0.0.1:8000
    python main.py serve --catalogue data/scale/catalogue.jsonl --policy data/scale/policy.txt --port 8080
    python main.py serve --embed-model PATH --llm-model PATH   # optional fallbacks, same as chat

Every reply is re-checked by the same independent grounding checker the evaluator uses, and the result is shown next
to the reply. Citation chips open the exact catalogue record or policy section the claim came from.

/insights is a retailer view across all chats since the server started: how questions were answered, what was asked
for but not stocked, and which questions needed clarifying. It keeps counts and short labels only, in memory; no
full conversations, no session ids, nothing written to disk.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from groundtruth import build_assistant
from groundtruth.paths import DATA_DIR, ROOT, SCALE_DIR, WEB_DIR

from .state import State
from .views import build_examples

MAX_MESSAGE = 500
DEMO_CATALOGUE, DEMO_POLICY = SCALE_DIR / "catalogue.jsonl", SCALE_DIR / "policy.txt"


def pick_data(catalogue: str | None, policy: str | None) -> tuple[str, str]:
    """Use what was passed; otherwise the 5,000-product demo catalogue, generating it on first run."""
    if catalogue:
        return catalogue, policy or str(DATA_DIR / "policy.txt")
    if not DEMO_CATALOGUE.exists():
        print("Generating the 5,000-product demo catalogue (first run only)...")
        subprocess.run([sys.executable, "-m", "tools.generate_scale"], check=True, cwd=ROOT,
                       stdout=subprocess.DEVNULL)
    return str(DEMO_CATALOGUE), policy or str(DEMO_POLICY)


def make_handler(state: State, page: bytes, info: dict, insights_page: bytes = b""):
    class Handler(BaseHTTPRequestHandler):
        server_version = "GroundTruth/1"

        def log_message(self, fmt, *args):  # quiet by default
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, data: dict) -> None:
            self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self):
            if self.path in ("/", "/index.html"):
                self._send(200, page, "text/html; charset=utf-8")
            elif self.path == "/insights" and insights_page:
                self._send(200, insights_page, "text/html; charset=utf-8")
            elif self.path == "/api/info":
                self._json(200, info)
            elif self.path == "/api/insights":
                self._json(200, state.insights.summary())
            elif self.path == "/api/insights.csv":
                self._send(200, state.insights.gaps_csv().encode("utf-8"), "text/csv; charset=utf-8")
            else:
                self._json(404, {"error": "Not found"})

        def do_POST(self):
            try:
                length = int(self.headers.get("Content-Length") or 0)
                if length > 10_000:
                    return self._json(413, {"error": "Message too large"})
                data = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError):
                return self._json(400, {"error": "Request body must be JSON"})
            sid = data.get("session_id")
            if self.path == "/api/chat":
                message = str(data.get("message", "")).strip()[:MAX_MESSAGE]
                if not message:
                    return self._json(400, {"error": "Type a question first"})
                try:
                    return self._json(200, state.chat(sid, message))
                except Exception as exc:  # never take the server down for one query
                    print(f"[web] error on {message!r}: {exc!r}", file=sys.stderr)
                    return self._json(500, {"error": "The assistant hit an error on that message"})
            if self.path == "/api/reset":
                return self._json(200, state.reset(sid))
            return self._json(404, {"error": "Not found"})

    return Handler


def add_arguments(ap: argparse.ArgumentParser) -> None:
    ap.add_argument("--catalogue", help="catalogue file (default: the 5,000-product demo, generated on first run)")
    ap.add_argument("--policy", help="policy file (default: the one that goes with the catalogue)")
    ap.add_argument("--embed-model")
    ap.add_argument("--llm-model")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--snapshot", help="date the catalogue prices were taken, e.g. 2026-10-01 "
                                       "(default: the catalogue file's last-modified date)")


def run(args: argparse.Namespace) -> None:
    args.catalogue, args.policy = pick_data(args.catalogue, args.policy)
    assistant = build_assistant(args.catalogue, args.policy, embed_model=args.embed_model, llm_model=args.llm_model)
    snapshot, source = args.snapshot, "given"
    if not snapshot:
        snapshot, source = datetime.fromtimestamp(Path(args.catalogue).stat().st_mtime).date().isoformat(), "file date"
    info = {"products": len(assistant.index.products), "policy_sections": len(assistant.sections),
            "semantic": assistant.semantic is not None, "llm": assistant.normaliser is not None,
            "snapshot": snapshot, "snapshot_source": source, "examples": build_examples(assistant)}
    page = (WEB_DIR / "index.html").read_bytes()
    insights_page = (WEB_DIR / "insights.html").read_bytes()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(State(assistant), page, info, insights_page))
    print(f"GroundTruth is running at http://{args.host}:{args.port}  ({info['products']} products, prices as of "
          f"{snapshot}). Retailer view: http://{args.host}:{args.port}/insights  Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
