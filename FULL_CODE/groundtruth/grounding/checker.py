"""Independent grounding checker, shared by the evaluator and the web demo's live check.

It re-derives what each citation licenses from the raw catalogue and policy text; it does not trust the assistant's
own bookkeeping. A reply is grounded when every citation resolves, every ₹ amount belongs to a cited product, cited
policy section or the user's own query, every other number appears in the cited sources, every product title named
is cited, every factual sentence carries a citation, every colour, material, brand or other attribute word comes
from the record cited beside it (or the shopper's own question), and every policy sentence is quoted word for word.
"""

from __future__ import annotations

import re

from ..catalogue import CatalogueIndex
from ..policy import Section
from ..text import STOPWORDS, field_label, format_inr, tokenize

CITE = re.compile(r"\[([^\]]+)\]")
POLICY_CITE = re.compile(r"\[policy:([^\]]+)\]")
RUPEE = re.compile(r"₹\s?(\d[\d,]*(?:\.\d+)?)")
NUMBER = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?")
UNITS = re.compile(r"(?<=[.!?])\s+(?!\[)|\n")  # sentences and lines; a citation stays with the sentence before it
BUDGET_WORD = re.compile(r"(?:under|over|below|above|between|within|upto|up to|than|and)\s*$", re.I)


def _numbers(text: str) -> set[str]:
    """Every number in the text, without thousands separators."""
    return {n.rstrip(",").replace(",", "") for n in NUMBER.findall(text)}


class Grounding:
    """Independent checker: re-derives what each citation licenses from the raw catalogue/policy."""

    def __init__(self, index: CatalogueIndex, sections: dict[str, Section]) -> None:
        self.index, self.sections = index, sections
        self.titles = sorted({p.title for p in index.products.values()}, key=len, reverse=True)
        self.title_ids: dict[str, set[str]] = {}
        for p in index.products.values():
            self.title_ids.setdefault(p.title, set()).add(p.id)
        # attribute names ("colour", "anti_slip") say what is talked about, not what it is: "tell me a colour" and
        # "does not list a water_resistance_rating" make no claim, so names are never checked as value words
        compound = {k for k in index.attr_values if "_" in k} | {field_label(k) for k in index.attr_values if "_" in k}
        names = "|".join(map(re.escape, sorted(compound, key=len, reverse=True)))
        self.attr_names = re.compile(rf"(?<!\w)(?:{names})(?!\w)" if names else r"(?!)")
        name_words = {t for k in index.attr_values if "_" not in k for t in tokenize(k)}
        # words that name a colour, material, brand, gender, ... in this catalogue (sizes and numbers excluded)
        self.attr_words = frozenset(
            t
            for values in index.attr_values.values()
            for tokens in values
            for t in tokens
            if len(t) >= 3 and not t.isdigit() and t not in STOPWORDS and t not in name_words
        )

    def source(self, cite: str) -> str | None:
        """The text a citation stands for (a catalogue record or policy section), or None if it doesn't exist."""
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
        """Problems with a reply's claims, the count of hallucinated prices, and its citations."""
        cites = list(dict.fromkeys(CITE.findall(text)))
        sources, problems = self._resolve(cites)
        body = CITE.sub("", text)
        hallucinated = self._hallucinated_prices(text, sources, query_text)
        problems += self._unsupported_numbers(body, sources, query_text)
        problems += self._uncited_titles(body, sources)
        problems += self._uncited_claims(text)
        problems += self._unsupported_attributes(text, query_text)
        problems += self._unquoted_policy(text)
        problems += [f"hallucinated price ₹{h}" for h in hallucinated]
        return {"problems": problems, "hallucinated_prices": len(hallucinated), "cited": cites}

    def _resolve(self, cites: list[str]) -> tuple[dict[str, str], list[str]]:
        """The source text of every citation that exists, and a problem for each one that doesn't."""
        sources, problems = {}, []
        for c in cites:
            src = self.source(c)
            if src is None:
                problems.append(f"unknown citation [{c}]")
            else:
                sources[c] = src
        return sources, problems

    def _hallucinated_prices(self, text: str, sources: dict[str, str], query_text: str) -> list[str]:
        """Every ₹ amount that is not the price (or policy amount) of the citation it sits next to.

        An amount belongs to the next citation after it in the same sentence or line ("... at ₹299 [P1]"), or the
        last one before it. The shopper's own numbers are allowed only as a budget ("under ₹500"), never as a price.
        """
        own = {
            float(n.replace(",", ""))
            for n in re.findall(r"\d[\d,]*(?:\.\d+)?", query_text)
            if n.replace(",", "").replace(".", "").isdigit()
        }
        wrong = []
        for unit in UNITS.split(text):
            cites = [(m.start(), m.group(1)) for m in CITE.finditer(unit)]
            for m in RUPEE.finditer(unit):
                value = float(m.group(1).replace(",", ""))
                if value in own and BUDGET_WORD.search(unit[max(0, m.start() - 12) : m.start()]):
                    continue  # the shopper's budget repeated back
                owner = next((c for at, c in cites if at > m.start()), cites[-1][1] if cites else None)
                allowed = {float(a.replace(",", "")) for a in RUPEE.findall(sources.get(owner, ""))}
                if value not in allowed:
                    wrong.append(m.group(1))
        return wrong

    @staticmethod
    def _unsupported_numbers(body: str, sources: dict[str, str], query_text: str) -> list[str]:
        """A problem for each number (other than a price or list marker) found in neither the sources nor the query."""
        allowed = _numbers(" ".join(sources.values()) + " " + query_text)
        stripped = RUPEE.sub("", re.sub(r"(?m)^\s*\d+[.)]\s", "", body))
        return [f"number {n} not in cited sources" for n in _numbers(stripped) if n not in allowed]

    def _uncited_titles(self, body: str, sources: dict[str, str]) -> list[str]:
        """A problem for each product title named in the reply whose product isn't cited."""
        return [
            f"names '{title}' without citing it"
            for title in self.titles
            if title in body and not (self.title_ids[title] & set(sources))
        ]

    def _uncited_claims(self, text: str) -> list[str]:
        """A problem for each sentence or line that states a fact (price, product, stock, rating) without a citation."""
        problems = []
        for unit in UNITS.split(text):
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
        return problems

    def _unsupported_attributes(self, text: str, query_text: str) -> list[str]:
        """A problem for each attribute word (colour, material, brand, ...) given by neither the record cited in the
        same sentence or line nor the shopper's own question, e.g. "the material is silk" for a cotton product."""
        asked = set(tokenize(query_text))
        problems = []
        for unit in UNITS.split(text):
            cited = " ".join(self.source(c) or "" for c in dict.fromkeys(CITE.findall(unit)))
            known = asked | set(tokenize(cited))
            words = set(tokenize(self.attr_names.sub(" ", CITE.sub("", unit)))) & self.attr_words
            problems += [f"'{w}' is not in the cited record" for w in sorted(words - known)]
        return problems

    def _unquoted_policy(self, text: str) -> list[str]:
        """A problem for each policy citation whose sentence is not a word-for-word sentence of that section."""
        problems = []
        for unit in UNITS.split(text):
            start = 0
            for m in POLICY_CITE.finditer(unit):
                said, start = unit[start : m.start()].strip(), m.end()
                section = self.sections.get(m.group(1))
                if section and not any(said.endswith(sentence) for sentence, _ in section.sentences):
                    problems.append(f"policy claim not quoted from [policy:{m.group(1)}]: {said[-60:]}")
        return problems
