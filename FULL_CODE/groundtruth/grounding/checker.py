"""Independent grounding checker, shared by the evaluator and the web demo's live check.

It re-derives what each citation licenses from the raw catalogue and policy text; it does not trust the assistant's
own bookkeeping. A reply is grounded when every citation resolves, every ₹ amount belongs to a cited product, cited
policy section or the user's own query, every other number appears in the cited sources, every product title named
is cited, and every factual sentence carries a citation.
"""

from __future__ import annotations

import re

from ..catalogue import CatalogueIndex
from ..policy import Section
from ..text import format_inr

CITE = re.compile(r"\[([^\]]+)\]")
RUPEE = re.compile(r"₹\s?(\d[\d,]*(?:\.\d+)?)")
NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> set[str]:
    return {n.rstrip(",").replace(",", "") for n in NUMBER.findall(text)}


class Grounding:
    """Independent checker: re-derives what each citation licenses from the raw catalogue/policy."""

    def __init__(self, index: CatalogueIndex, sections: dict[str, Section]):
        self.index, self.sections = index, sections
        self.titles = sorted({p.title for p in index.products.values()}, key=len, reverse=True)
        self.title_ids: dict[str, set[str]] = {}
        for p in index.products.values():
            self.title_ids.setdefault(p.title, set()).add(p.id)

    def source(self, cite: str) -> str | None:
        if cite.startswith("policy:"):
            sec = self.sections.get(cite[7:])
            return f"{sec.heading} {sec.body}" if sec else None
        p = self.index.products.get(cite)
        if not p:
            return None
        attrs = " ".join(f"{k} {' '.join(p.attr_values(k))}" for k in p.attributes)
        return (
            f"{p.id} {p.title} {p.description} {p.category} {attrs} {p.price:g} {format_inr(p.price)} "
            f"{p.rating if p.rating is not None else ''} {p.rating_count or ''}"
        )

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
            p = self.index.products.get(c)
            if p:
                allowed_money.add(float(p.price))
        allowed_money |= {
            float(n.replace(",", ""))
            for n in re.findall(r"\d[\d,]*(?:\.\d+)?", query_text)
            if n.replace(",", "").replace(".", "").isdigit()
        }
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
            states_fact = (
                bool(RUPEE.search(u))
                or any(t in u for t in self.titles)
                or bool(re.search(r"\b(in stock|out of stock|rated|stars)\b", u))
            )
            if states_fact and not CITE.search(u):
                problems.append(f"uncited claim: {u[:60]}")
        problems += [f"hallucinated price ₹{h}" for h in hallucinated]
        return {"problems": problems, "hallucinated_prices": len(hallucinated), "cited": cites}
