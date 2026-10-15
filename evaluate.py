"""Score the assistant on groundedness, helpfulness and refusal accuracy, plus a latency / token-cost report.

    python evaluate.py [--questions data/test_questions.jsonl] [--out reports]

Question format (one JSON object per line); extra keys are ignored:
    {"id": "q1", "turns": ["...", "..."],            # or "question": "..."  (a single turn)
     "expected": {"behaviour": "answer" | "clarify" | "refuse",
                  "product_ids": [...],              # all must be cited (helpfulness)
                  "valid_ids": [...],                # every product satisfying the request; cited ids must come from here
                  "policy_sections": [...],          # at least one must be cited
                  "must_include": ["₹2,499", ...]}}  # substrings (case-insensitive) the reply must contain
Adapt `load_cases` if the real test file uses other field names.

Scoring
  groundedness   share of replies with zero unsupported claims (checked from the reply text, not from the
                 assistant's own bookkeeping): every citation resolves, every ₹ amount belongs to a cited
                 product / policy section / the user's own query, every other number appears in the cited
                 sources, every product title named is cited, every factual sentence carries a citation.
  helpfulness    over questions that should be answered: cited the right product(s)/policy, satisfied the
                 filters (precision), and included the required facts.
  refusal acc.   behaviour (answer / clarify / refuse) matches expectation, over all questions.
  hallucinated prices are counted separately and subtract 0.10 from the final score each, floor 0.
  final          0.35 * groundedness + 0.35 * helpfulness + 0.30 * refusal accuracy - 0.10 * hallucinated prices
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import sys
from pathlib import Path

from shopassistant import Session, build_assistant
from shopassistant.text import format_inr

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

CITE = re.compile(r"\[([^\]]+)\]")
RUPEE = re.compile(r"₹\s?(\d[\d,]*(?:\.\d+)?)")
NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")
FINAL_WEIGHTS = (0.35, 0.35, 0.30)
HALLUCINATED_PRICE_PENALTY = 0.10


def load_cases(path: Path) -> list[dict]:
    cases = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        turns = raw.get("turns") or [raw.get("question") or raw.get("query") or raw.get("q")]
        expected = raw.get("expected") or {k: v for k, v in raw.items() if k not in {"id", "turns", "question", "query"}}
        cases.append({"id": raw.get("id", f"q{len(cases) + 1}"), "turns": turns, "expected": expected})
    return cases


def _numbers(text: str) -> set[str]:
    return {n.rstrip(",").replace(",", "") for n in NUMBER.findall(text)}


class Grounding:
    """Independent checker: re-derives what each citation licenses from the raw catalogue/policy."""

    def __init__(self, assistant):
        self.a = assistant
        self.titles = sorted({p.title for p in assistant.index.products.values()}, key=len, reverse=True)
        self.title_ids: dict[str, set[str]] = {}
        for p in assistant.index.products.values():
            self.title_ids.setdefault(p.title, set()).add(p.id)

    def source(self, cite: str) -> str | None:
        if cite.startswith("policy:"):
            sec = self.a.sections.get(cite[7:])
            return f"{sec.heading} {sec.body}" if sec else None
        p = self.a.index.products.get(cite)
        if not p:
            return None
        attrs = " ".join(f"{k} {' '.join(p.attr_values(k))}" for k in p.attributes)
        return (f"{p.id} {p.title} {p.description} {p.category} {attrs} {p.price:g} {format_inr(p.price)} "
                f"{p.rating if p.rating is not None else ''} {p.rating_count or ''}")

    def check(self, text: str, query_text: str) -> dict:
        cites = list(dict.fromkeys(CITE.findall(text)))
        problems, hallucinated = [], []
        sources = {}
        for c in cites:
            src = self.source(c)
            if src is None:
                problems.append(f"unknown citation [{c}]")
            else:
                sources[c] = src
        body = CITE.sub("", text)
        allowed_money = {float(m.replace(",", "")) for src in sources.values() for m in RUPEE.findall(src)}
        for c in sources:
            p = self.a.index.products.get(c)
            if p:
                allowed_money.add(float(p.price))
        allowed_money |= {float(n.replace(",", "")) for n in re.findall(r"\d[\d,]*(?:\.\d+)?", query_text)
                          if n.replace(",", "").replace(".", "").isdigit()}
        for m in RUPEE.findall(body):
            if float(m.replace(",", "")) not in allowed_money:
                hallucinated.append(m)
        allowed_nums = _numbers(" ".join(sources.values()) + " " + query_text)
        for n in _numbers(RUPEE.sub("", re.sub(r"(?m)^\s*\d+[.)]\s", "", body))):
            if n not in allowed_nums:
                problems.append(f"number {n} not in cited sources")
        for title in self.titles:
            if title in body and not (self.title_ids[title] & set(sources)):
                problems.append(f"names '{title}' without citing it")
        # every sentence/line that states a fact must carry a citation
        for unit in re.split(r"(?<=[.!?])\s+(?!\[)|\n", text):
            u = unit.strip(" -")
            if not u:
                continue
            states_fact = bool(RUPEE.search(u)) or any(t in u for t in self.titles) or \
                bool(re.search(r"\b(in stock|out of stock|rated|stars)\b", u))
            if states_fact and not CITE.search(u):
                problems.append(f"uncited claim: {u[:60]}")
        problems += [f"hallucinated price ₹{h}" for h in hallucinated]
        return {"problems": problems, "hallucinated_prices": len(hallucinated), "cited": cites}


def helpfulness(resp, exp: dict, cited: list[str]) -> float:
    if exp.get("behaviour") != "answer":
        return 1.0 if resp.action == exp.get("behaviour") else 0.0
    if resp.action != "answer":
        return 0.0
    checks = []
    ids = [c for c in cited if not c.startswith("policy:")]
    if exp.get("product_ids"):
        checks.append(all(i in ids for i in exp["product_ids"]))
    if exp.get("valid_ids") is not None:
        checks.append(bool(ids) and all(i in exp["valid_ids"] for i in ids))
    if exp.get("policy_sections"):
        checks.append(any(c == f"policy:{s}" for s in exp["policy_sections"] for c in cited))
    low = resp.text.lower()
    for needle in exp.get("must_include", []):
        checks.append(needle.lower() in low)
    return sum(checks) / len(checks) if checks else 1.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalogue", default="data/catalogue.jsonl")
    ap.add_argument("--policy", default="data/policy.txt")
    ap.add_argument("--questions", default="data/test_questions.jsonl")
    ap.add_argument("--out", default="reports")
    ap.add_argument("--price-in", type=float, default=0.0, help="USD per 1M input tokens (0 for the rules-based assistant)")
    ap.add_argument("--price-out", type=float, default=0.0, help="USD per 1M output tokens")
    ap.add_argument("--verbose", action="store_true", help="print every failing case")
    args = ap.parse_args()

    assistant = build_assistant(args.catalogue, args.policy, measure_tokens=True)
    grounding = Grounding(assistant)
    cases = load_cases(Path(args.questions))
    rows, confusion = [], {}
    for case in cases:
        session, resp = Session(), None
        for turn in case["turns"]:
            resp = assistant.ask(turn, session)
        exp = case["expected"]
        accept = exp["behaviour"] if isinstance(exp.get("behaviour"), list) else [exp.get("behaviour")]
        exp = {**exp, "behaviour": resp.action if resp.action in accept else accept[0]}
        g = grounding.check(resp.text, " ".join(case["turns"]))
        behaviour_ok = resp.action in accept
        confusion[(exp.get("behaviour"), resp.action)] = confusion.get((exp.get("behaviour"), resp.action), 0) + 1
        rows.append({
            "id": case["id"], "query": " | ".join(case["turns"]), "expected": exp.get("behaviour"),
            "action": resp.action, "behaviour_ok": behaviour_ok, "grounded": not g["problems"],
            "hallucinated_prices": g["hallucinated_prices"], "helpfulness": helpfulness(resp, exp, g["cited"]),
            "problems": "; ".join(g["problems"]), "latency_ms": round(resp.latency_ms, 3),
            "context_tokens": resp.context_tokens, "output_tokens": resp.output_tokens, "reply": resp.text,
        })

    n = len(rows)
    answerable = [r for r in rows if r["expected"] == "answer"]
    groundedness = sum(r["grounded"] for r in rows) / n
    helpful = sum(r["helpfulness"] for r in answerable) / max(1, len(answerable))
    refusal_acc = sum(r["behaviour_ok"] for r in rows) / n
    hallucinated = sum(r["hallucinated_prices"] for r in rows)
    wg, wh, wr = FINAL_WEIGHTS
    final = max(0.0, wg * groundedness + wh * helpful + wr * refusal_acc - HALLUCINATED_PRICE_PENALTY * hallucinated)

    def rate(expected: str):
        sub = [r for r in rows if r["expected"] == expected]
        return (sum(r["behaviour_ok"] for r in sub) / len(sub)) if sub else float("nan"), len(sub)

    lat = [r["latency_ms"] for r in rows]
    ctx = [r["context_tokens"] for r in rows]
    out = [r["output_tokens"] for r in rows]
    cost = sum(c * args.price_in + o * args.price_out for c, o in zip(ctx, out)) / 1e6
    summary = {
        "questions": n, "groundedness": round(groundedness, 4), "helpfulness": round(helpful, 4),
        "refusal_accuracy": round(refusal_acc, 4), "hallucinated_prices": hallucinated, "final_score": round(final, 4),
        "correct_answer_rate": rate("answer")[0], "correct_clarify_rate": rate("clarify")[0],
        "correct_refusal_rate": rate("refuse")[0],
        "latency_ms": {"mean": round(statistics.mean(lat), 3), "p50": round(statistics.median(lat), 3),
                       "p95": round(statistics.quantiles(lat, n=20)[-1], 3), "max": round(max(lat), 3)},
        "tokens_per_query": {"context_mean": round(statistics.mean(ctx), 1), "output_mean": round(statistics.mean(out), 1),
                             "context_total": sum(ctx), "output_total": sum(out),
                             "note": "estimates (~4 chars/token): context = query + cited sources an LLM would be given; "
                                     "this assistant makes zero LLM calls"},
        "estimated_cost_usd": round(cost, 6),
        "confusion": {f"{a}->{b}": c for (a, b), c in sorted(confusion.items(), key=str)},
    }

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "query_report.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    failing = [r for r in rows if not (r["behaviour_ok"] and r["grounded"] and r["helpfulness"] == 1.0)]
    print(f"\n{len(failing)} of {n} questions have a failure (see {out_dir / 'query_report.csv'})")
    if args.verbose:
        for r in failing:
            print(f"\n[{r['id']}] {r['query']}\n  expected={r['expected']} got={r['action']} help={r['helpfulness']:.2f} "
                  f"grounded={r['grounded']} {r['problems']}\n  reply: {r['reply'][:300]}")


if __name__ == "__main__":
    main()
