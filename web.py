"""Local web UI for the shopping assistant. Standard library only, fully offline.

    python3 web.py                       # http://127.0.0.1:8000
    python3 web.py --catalogue data/scale/catalogue.jsonl --policy data/scale/policy.txt --port 8080
    python3 web.py --embed-model PATH --llm-model PATH   # optional fallbacks, same as chat.py

Every reply is re-checked by the same independent grounding checker evaluate.py uses, and the result is shown next
to the reply. Citation chips open the exact catalogue record or policy section the claim came from.

/insights is a retailer view across all chats since the server started: how questions were answered, what was asked
for but not stocked, and which questions needed clarifying. It keeps counts and short labels only, in memory; no
full conversations, no session ids, nothing written to disk.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import secrets
import statistics
import sys
import threading
import time
from collections import Counter, OrderedDict, deque
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from evaluate import Grounding
from shopassistant import CLARIFY, REFUSE, Session, build_assistant
from shopassistant.text import format_inr
from shopassistant.understand import understand

ROOT = Path(__file__).resolve().parent
MAX_SESSIONS = 500
MAX_MESSAGE = 500


def product_record(p) -> dict:
    return {"kind": "product", "id": p.id, "title": p.title, "price": format_inr(p.price), "category": p.category,
            "in_stock": p.in_stock, "rating": p.rating, "rating_count": p.rating_count,
            "description": p.description, "attributes": {k: p.attr_values(k) for k in p.attributes}}


def policy_record(sec) -> dict:
    return {"kind": "policy", "id": f"policy:{sec.id}", "title": sec.heading, "body": sec.body}


def reading(parsed) -> dict:
    """How the query was understood, in shopper terms (no internals)."""
    s = parsed.spec
    return {"looking_for": s.type_tokens, "features": s.desc_tokens, "filters": s.filters,
            "excluding": s.exclude, "budget_min": s.price_min, "budget_max": s.price_max, "size": s.size,
            "sort": s.sort, "not_recognised": parsed.unknown, "brands_not_carried": parsed.external,
            "read_as": [list(m) for m in parsed.mapped], "asking_about": sorted(parsed.intents)}


def remembered(session: Session) -> dict:
    s = session.spec
    return {"looking_for": s.type_tokens, "features": s.desc_tokens, "filters": s.filters, "excluding": s.exclude,
            "budget_min": s.price_min, "budget_max": s.price_max, "size": s.size, "sort": s.sort,
            "last_product": session.last_product}


class Insights:
    """Counts across all chats since start-up, for the retailer view. Labels are capped in length and number."""

    def __init__(self):
        self.lock = threading.Lock()
        self.started = time.time()
        self.actions: Counter = Counter()
        self.paths: Counter = Counter()
        self.gaps: Counter = Counter()
        self.unclear: Counter = Counter()
        self.failed_checks = 0
        self.latency = deque(maxlen=5000)
        self.llm_tokens = 0

    def record(self, action: str, path: str, ms: float, gap_labels: list[str], unclear: str | None,
               check_ok: bool, llm_tokens: int) -> None:
        with self.lock:
            self.actions[action] += 1
            self.paths[path] += 1
            self.latency.append(ms)
            self.llm_tokens += llm_tokens
            self.failed_checks += 0 if check_ok else 1
            for g in gap_labels:
                if len(self.gaps) < 2000 or g in self.gaps:
                    self.gaps[g[:60]] += 1
            if unclear and (len(self.unclear) < 2000 or unclear in self.unclear):
                self.unclear[unclear[:60]] += 1

    def summary(self) -> dict:
        with self.lock:
            n = sum(self.actions.values())
            lat = sorted(self.latency)
            pct = lambda q: round(lat[min(len(lat) - 1, int(q * len(lat)))], 3) if lat else None  # noqa: E731
            return {
                "since": datetime.fromtimestamp(self.started).isoformat(timespec="seconds"),
                "questions": n,
                "actions": {a: self.actions.get(a, 0) for a in ("answer", "clarify", "refuse")},
                "paths": {p: self.paths.get(p, 0) for p in ("rules", "semantic", "llm")},
                "latency_ms": {"median": round(statistics.median(lat), 3) if lat else None, "p95": pct(.95)},
                "llm_tokens": self.llm_tokens,
                "failed_checks": self.failed_checks,
                "not_stocked": self.gaps.most_common(15),
                "needed_clarifying": self.unclear.most_common(10),
            }

    def gaps_csv(self) -> str:
        with self.lock:
            out = io.StringIO()
            w = csv.writer(out)
            w.writerow(["requested_item", "times_refused"])
            w.writerows(self.gaps.most_common())
            return out.getvalue()


class State:
    def __init__(self, assistant):
        self.assistant = assistant
        self.grounding = Grounding(assistant)
        self.sessions: OrderedDict[str, dict] = OrderedDict()
        self.lock = threading.Lock()
        self.insights = Insights()

    def session(self, sid: str | None) -> tuple[str, dict]:
        with self.lock:
            if not sid or sid not in self.sessions:
                sid = secrets.token_urlsafe(12)
                self.sessions[sid] = {"session": Session(), "lock": threading.Lock(), "gaps": Counter(),
                                      "totals": {"queries": 0, "ms": 0.0, "llm_tokens": 0}}
                while len(self.sessions) > MAX_SESSIONS:
                    self.sessions.popitem(last=False)
            self.sessions.move_to_end(sid)
            return sid, self.sessions[sid]

    def chat(self, sid: str | None, message: str) -> dict:
        sid, entry = self.session(sid)
        a = self.assistant
        with entry["lock"]:
            start = time.perf_counter()
            resp = a.ask(message, entry["session"])
            check = self.grounding.check(resp.text, message)
            parsed = understand(message, a.index)
            total_ms = (time.perf_counter() - start) * 1000
            sources = {}
            for c in resp.citations:
                if c.startswith("policy:") and c[7:] in a.sections:
                    sources[c] = policy_record(a.sections[c[7:]])
                elif c in a.index.products:
                    sources[c] = product_record(a.index.products[c])
            gap_labels = []
            if resp.action == REFUSE:
                gap_labels = parsed.unknown or [self._wanted(parsed, entry["session"]) or message.strip().lower()]
                for w in gap_labels:
                    entry["gaps"][w] += 1
            self.insights.record(resp.action, resp.path, resp.latency_ms, gap_labels,
                                 (self._wanted(parsed, entry["session"]) or None) if resp.action == CLARIFY else None,
                                 not check["problems"], resp.llm_input_tokens + resp.llm_output_tokens)
            t = entry["totals"]
            t["queries"] += 1
            t["ms"] += resp.latency_ms
            t["llm_tokens"] += resp.llm_input_tokens + resp.llm_output_tokens
            return {
                "session_id": sid, "text": resp.text, "action": resp.action, "sources": sources,
                "verified": {"ok": not check["problems"], "problems": check["problems"],
                             "cited": len(check["cited"])},
                "metrics": {"engine_ms": round(resp.latency_ms, 3), "with_checks_ms": round(total_ms, 3),
                            "path": resp.path, "llm_input_tokens": resp.llm_input_tokens,
                            "llm_output_tokens": resp.llm_output_tokens},
                "reading": reading(parsed), "remembered": remembered(entry["session"]),
                "gaps": entry["gaps"].most_common(8),
                "totals": {"queries": t["queries"], "mean_ms": round(t["ms"] / t["queries"], 3),
                           "llm_tokens": t["llm_tokens"]},
            }

    @staticmethod
    def _wanted(parsed, session: Session) -> str:
        """What a refused request was for, combining this message with the chat so far ("cotton running shoe")."""
        s = session.spec
        if parsed.spec.desc_tokens and not (parsed.spec.type_tokens or parsed.spec.filters):
            return " ".join(parsed.spec.desc_tokens)  # "laptop" after "black hoodie" is a request for a laptop
        words = list(dict.fromkeys(list(parsed.spec.filters.values()) + list(s.filters.values())
                                   + (parsed.spec.type_tokens or s.type_tokens) + parsed.spec.desc_tokens))
        budget = parsed.spec.price_max if parsed.spec.price_max is not None else s.price_max
        label = " ".join(words)
        if label and budget is not None:
            label += f", under {format_inr(budget)}"
        return label

    def reset(self, sid: str | None) -> dict:
        sid, entry = self.session(sid)
        with entry["lock"]:
            entry["session"].clear()
        return {"session_id": sid, "remembered": remembered(entry["session"])}


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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--catalogue", default="data/catalogue.jsonl")
    ap.add_argument("--policy", default="data/policy.txt")
    ap.add_argument("--embed-model")
    ap.add_argument("--llm-model")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--snapshot", help="date the catalogue prices were taken, e.g. 2026-10-01 "
                                       "(default: the catalogue file's last-modified date)")
    args = ap.parse_args()
    assistant = build_assistant(args.catalogue, args.policy, embed_model=args.embed_model, llm_model=args.llm_model)
    snapshot, source = args.snapshot, "given"
    if not snapshot:
        snapshot, source = datetime.fromtimestamp(Path(args.catalogue).stat().st_mtime).date().isoformat(), "file date"
    info = {"products": len(assistant.index.products), "policy_sections": len(assistant.sections),
            "semantic": assistant.semantic is not None, "llm": assistant.normaliser is not None,
            "snapshot": snapshot, "snapshot_source": source}
    page = (ROOT / "web" / "index.html").read_bytes()
    insights_page = (ROOT / "web" / "insights.html").read_bytes()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(State(assistant), page, info, insights_page))
    print(f"GroundTruth is running at http://{args.host}:{args.port}  ({info['products']} products, prices as of "
          f"{snapshot}). Retailer view: http://{args.host}:{args.port}/insights  Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
