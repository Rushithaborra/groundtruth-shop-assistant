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

from groundtruth.answering import CLARIFY, REFUSE, Session
from groundtruth.config import get_settings
from groundtruth.grounding import Grounding
from groundtruth.models.semantic import type_words
from groundtruth.text import format_inr
from groundtruth.understanding import understand

from .views import policy_record, product_record, reading, remembered


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
                "latency_ms": {"median": round(statistics.median(lat), 3) if lat else None, "p95": pct(0.95)},
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
        self.grounding = Grounding(assistant.index, assistant.sections)
        self.sessions: OrderedDict[str, dict] = OrderedDict()
        self.lock = threading.Lock()
        self.insights = Insights()
        self.kinds = frozenset(type_words(assistant.index))
        self.max_sessions = get_settings().server.max_sessions

    def session(self, sid: str | None) -> tuple[str, dict]:
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
        sid, entry = self.session(sid)
        a = self.assistant
        with entry["lock"]:
            start = time.perf_counter()
            resp = a.ask(message, entry["session"])
            entry["said"].append(message)
            # like evaluate.py, numbers the shopper said earlier in the chat (a budget) are theirs, not invented
            check = self.grounding.check(resp.text, " ".join(entry["said"]))
            parsed = understand(message, a.index)
            total_ms = (time.perf_counter() - start) * 1000
            sources = {}
            for c in resp.citations:
                if c.startswith("policy:") and c[7:] in a.sections:
                    sources[c] = policy_record(a.sections[c[7:]])
                elif c in a.index.products:
                    sources[c] = product_record(a.index.products[c], self.kinds)
            gap_labels = []
            if resp.action == REFUSE and "account" not in parsed.intents:  # orders/coupons aren't products
                gap_labels = parsed.unknown or [self._wanted(parsed, entry["session"]) or message.strip().lower()]
                for w in gap_labels:
                    entry["gaps"][w] += 1
            self.insights.record(
                resp.action,
                resp.path,
                resp.latency_ms,
                gap_labels,
                (self._wanted(parsed, entry["session"]) or None) if resp.action == CLARIFY else None,
                not check["problems"],
                resp.llm_input_tokens + resp.llm_output_tokens,
            )
            t = entry["totals"]
            t["queries"] += 1
            t["ms"] += resp.latency_ms
            t["llm_tokens"] += resp.llm_input_tokens + resp.llm_output_tokens
            return {
                "session_id": sid,
                "text": resp.text,
                "action": resp.action,
                "sources": sources,
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

    @staticmethod
    def _wanted(parsed, session: Session) -> str:
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
        sid, entry = self.session(sid)
        with entry["lock"]:
            entry["session"].clear()
            entry["said"].clear()
        return {"session_id": sid, "remembered": remembered(entry["session"])}
