"""Server-side state for the web demo: per-visitor sessions and the retailer insights counters."""

from __future__ import annotations

import csv
import io
import secrets
import statistics
import threading
import time
from collections import Counter, OrderedDict, deque
from datetime import datetime

from groundtruth.answering import CLARIFY, REFUSE, Response, Session, ShoppingAssistant
from groundtruth.config import get_settings
from groundtruth.grounding import Grounding
from groundtruth.models.semantic import type_words
from groundtruth.text import format_inr
from groundtruth.understanding import Parsed, understand

from .views import policy_record, product_record, reading, remembered


class Insights:
    """Counts across all chats since start-up, for the retailer view. Labels are capped in length and number."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.started = time.time()
        self.actions: Counter = Counter()
        self.paths: Counter = Counter()
        self.gaps: Counter = Counter()
        self.unclear: Counter = Counter()
        self.failed_checks = 0
        self.latency = deque(maxlen=5000)
        self.llm_tokens = 0

    def record(
        self,
        action: str,
        path: str,
        ms: float,
        gap_labels: list[str],
        unclear: str | None,
        check_ok: bool,
        llm_tokens: int,
    ) -> None:
        """Count one answered message for the retailer view (labels are capped in length and number)."""
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
        """The retailer view's numbers: actions, paths, latency, refused requests, clarified requests."""
        with self.lock:
            n = sum(self.actions.values())
            lat = sorted(self.latency)
            pct = lambda q: round(lat[min(len(lat) - 1, int(q * len(lat)))], 3) if lat else None  # noqa: E731
            return {
                "since": datetime.fromtimestamp(self.started).isoformat(timespec="seconds"),
                "questions": n,
                "actions": {a: self.actions.get(a, 0) for a in ("answer", "clarify", "refuse")},
                "paths": {p: self.paths.get(p, 0) for p in ("rules", "semantic", "llm")},
                "latency_ms": {"median": round(statistics.median(lat), 3) if lat else None, "p95": pct(0.95)},
                "llm_tokens": self.llm_tokens,
                "failed_checks": self.failed_checks,
                "not_stocked": self.gaps.most_common(15),
                "needed_clarifying": self.unclear.most_common(10),
            }

    def gaps_csv(self) -> str:
        """Requested-but-not-stocked items and how often they were refused, as CSV."""
        with self.lock:
            out = io.StringIO()
            w = csv.writer(out)
            w.writerow(["requested_item", "times_refused"])
            w.writerows(self.gaps.most_common())
            return out.getvalue()


class State:
    def __init__(self, assistant: ShoppingAssistant) -> None:
        self.assistant = assistant
        self.grounding = Grounding(assistant.index, assistant.sections)
        self.sessions: OrderedDict[str, dict] = OrderedDict()
        self.lock = threading.Lock()
        self.insights = Insights()
        self.kinds = frozenset(type_words(assistant.index))
        self.max_sessions = get_settings().server.max_sessions

    def session(self, sid: str | None) -> tuple[str, dict]:
        """The visitor's session, creating one for a missing or unknown id; least recently used are dropped."""
        with self.lock:
            if not sid or sid not in self.sessions:
                sid = secrets.token_urlsafe(12)
                self.sessions[sid] = {
                    "session": Session(),
                    "lock": threading.Lock(),
                    "gaps": Counter(),
                    "said": deque(maxlen=30),
                    "totals": {"queries": 0, "ms": 0.0, "llm_tokens": 0},
                }
                while len(self.sessions) > self.max_sessions:
                    self.sessions.popitem(last=False)
            self.sessions.move_to_end(sid)
            return sid, self.sessions[sid]

    def chat(self, sid: str | None, message: str) -> dict:
        """Answer one message in a visitor's session; returns the reply with its sources, check and metrics."""
        sid, entry = self.session(sid)
        with entry["lock"]:
            start = time.perf_counter()
            resp = self.assistant.ask(message, entry["session"])
            entry["said"].append(message)
            # like the evaluator, numbers the shopper said earlier in the chat (a budget) are theirs, not invented
            check = self.grounding.check(resp.text, " ".join(entry["said"]))
            parsed = understand(message, self.assistant.index)
            total_ms = (time.perf_counter() - start) * 1000
            self._record(resp, parsed, entry, message, check_ok=not check["problems"])
            t = entry["totals"]
            return {
                "session_id": sid,
                "text": resp.text,
                "action": resp.action,
                "sources": self._sources(resp.citations),
                "verified": {"ok": not check["problems"], "problems": check["problems"], "cited": len(check["cited"])},
                "metrics": {
                    "engine_ms": round(resp.latency_ms, 3),
                    "with_checks_ms": round(total_ms, 3),
                    "path": resp.path,
                    "llm_input_tokens": resp.llm_input_tokens,
                    "llm_output_tokens": resp.llm_output_tokens,
                },
                "reading": reading(parsed),
                "remembered": remembered(entry["session"]),
                "gaps": entry["gaps"].most_common(8),
                "totals": {
                    "queries": t["queries"],
                    "mean_ms": round(t["ms"] / t["queries"], 3),
                    "llm_tokens": t["llm_tokens"],
                },
            }

    def _sources(self, citations: list[str]) -> dict:
        """The catalogue record or policy section behind each citation, for the cards and receipts."""
        a, sources = self.assistant, {}
        for c in citations:
            if c.startswith("policy:") and c[7:] in a.sections:
                sources[c] = policy_record(a.sections[c[7:]])
            elif c in a.index.products:
                sources[c] = product_record(a.index.products[c], self.kinds)
        return sources

    def _record(self, resp: Response, parsed: Parsed, entry: dict, message: str, check_ok: bool) -> None:
        """Update this visitor's totals and refused requests, and the retailer view's counters."""
        gap_labels = []
        # only requests for products count as demand: not orders or coupons ("account"), not policy questions, and
        # not a question about a listed product's details ("paani me chalega?")
        not_a_product_request = parsed.intents & {"account", "policy"} or resp.claims.sections or parsed.detail_keys
        if resp.action == REFUSE and not not_a_product_request:
            gap_labels = parsed.unknown or [self._wanted(parsed, entry["session"]) or message.strip().lower()]
            for w in gap_labels:
                entry["gaps"][w] += 1
        unclear = (self._wanted(parsed, entry["session"]) or None) if resp.action == CLARIFY else None
        tokens = resp.llm_input_tokens + resp.llm_output_tokens
        self.insights.record(resp.action, resp.path, resp.latency_ms, gap_labels, unclear, check_ok, tokens)
        t = entry["totals"]
        t["queries"] += 1
        t["ms"] += resp.latency_ms
        t["llm_tokens"] += tokens

    @staticmethod
    def _wanted(parsed: Parsed, session: Session) -> str:
        """What a refused request was for, combining this message with the chat so far ("cotton running shoe")."""
        s = session.spec
        if parsed.spec.desc_tokens and not (parsed.spec.type_tokens or parsed.spec.filters):
            return " ".join(parsed.spec.desc_tokens)  # "laptop" after "black hoodie" is a request for a laptop
        if parsed.spec.type_tokens:  # a product named in this message is a new request: earlier filters don't apply
            s = type(s)()
        words = list(
            dict.fromkeys(
                list(parsed.spec.filters.values())
                + list(s.filters.values())
                + (parsed.spec.type_tokens or s.type_tokens)
                + parsed.spec.desc_tokens
            )
        )
        budget = parsed.spec.price_max if parsed.spec.price_max is not None else s.price_max
        label = " ".join(words)
        if label and budget is not None:
            label += f", under {format_inr(budget)}"
        return label

    def reset(self, sid: str | None) -> dict:
        """Forget a visitor's remembered filters, budget and chat so far."""
        sid, entry = self.session(sid)
        with entry["lock"]:
            entry["session"].clear()
            entry["said"].clear()
        return {"session_id": sid, "remembered": remembered(entry["session"])}
