"""The grounded shopping assistant.

Every sentence is assembled from catalogue fields or extracted from the policy text, and carries a citation
([P0001] for a product, [policy:returns] for a policy section). Nothing is generated freely, so a price that
is not in the catalogue cannot be produced. When the catalogue cannot support an answer it refuses; when
several products fit a question about "the" product it asks which one.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from .catalogue import Product
from .index import CatalogueIndex, SIZE_KEYS, Spec
from .policy import Section, best_sentences
from .text import approx_tokens, format_inr, split_sentences, tokenize
from .understand import Parsed, understand

ANSWER, CLARIFY, REFUSE = "answer", "clarify", "refuse"
CITATION = re.compile(r"\[([^\]]+)\]")


@dataclass
class Response:
    text: str
    action: str
    claims: list[dict] = field(default_factory=list)
    latency_ms: float = 0.0
    context_tokens: int = 0
    output_tokens: int = 0

    @property
    def citations(self) -> list[str]:
        return list(dict.fromkeys(CITATION.findall(self.text)))


@dataclass
class Session:
    spec: Spec = field(default_factory=Spec)
    last_product: str | None = None

    def clear(self) -> None:
        self.spec, self.last_product = Spec(), None


class ShoppingAssistant:
    SORT_LABELS = {"price_asc": "lowest-priced", "price_desc": "highest-priced", "rating_desc": "highest-rated"}

    def __init__(self, index: CatalogueIndex, policy: list[Section], list_limit: int = 3, clarify_limit: int = 5):
        self.index, self.policy = index, policy
        self.sections = {s.id: s for s in policy}
        self.list_limit, self.clarify_limit = list_limit, clarify_limit

    # -- public --------------------------------------------------------------------------------
    def ask(self, query: str, session: Session | None = None) -> Response:
        session = session if session is not None else Session()
        start = time.perf_counter()
        resp = self._route(understand(query, self.index), session)
        resp.latency_ms = (time.perf_counter() - start) * 1000
        resp.output_tokens = approx_tokens(resp.text)
        resp.context_tokens = approx_tokens(query) + sum(approx_tokens(self._source_text(c)) for c in resp.citations)
        return resp

    def _source_text(self, citation: str) -> str:
        if citation.startswith("policy:"):
            sec = self.sections.get(citation[7:])
            return f"{sec.heading}. {sec.body}" if sec else ""
        p = self.index.products.get(citation)
        if not p:
            return ""
        attrs = "; ".join(f"{k}: {', '.join(p.attr_values(k))}" for k in p.attributes)
        return f"{p.id} {p.title}. {p.description} {p.category}. {attrs}. {format_inr(p.price)}"

    # -- routing -------------------------------------------------------------------------------
    def _route(self, p: Parsed, session: Session) -> Response:
        if "reset" in p.intents:
            session.clear()
            return Response("Okay, starting fresh. What are you looking for?", CLARIFY)
        if "greet" in p.intents:
            return Response("Hi! I can help you find products, check price, stock and ratings, and answer "
                            "questions about returns, shipping and warranty. What do you need?", CLARIFY)
        if "policy" in p.intents:
            return self._policy(p)

        forced, spec = self._resolve(p, session)
        if forced is not None:
            session.last_product = forced.id
            return self._product_answer(forced, p)

        if not spec.type_tokens and not spec.filters and spec.desc_tokens and session.spec.is_empty():
            # only description words (e.g. "laptop" appearing in a backpack's text): not a product the shop sells
            p.unknown = p.unknown + spec.desc_tokens
            p.spec.desc_tokens = []
            spec.desc_tokens = []
        known = len(p.spec.type_tokens) + len(p.spec.desc_tokens) + len(p.spec.filters)
        if p.unknown and len(p.unknown) >= max(known, 1):
            return self._refuse(f"I don't have anything in the catalogue matching \"{' '.join(p.unknown)}\".")
        if spec.is_empty():
            return Response("What are you looking for? Tell me the kind of product, and a brand, colour or budget "
                            "if you have one.", CLARIFY)

        hits, relaxed = self.index.search(spec, p.all_tokens)
        if not hits:
            return self._no_match(spec, p)
        session.spec = spec

        specific = p.intents & {"price", "rating", "detail"}
        if specific and len(hits) > 1:
            if spec.sort:
                pick = hits[0]
            elif spec.price_max is not None or spec.price_min is not None:
                pick = None  # a budget implies browsing, not one specific product
            else:
                exact = self.index.exact_title_matches(hits, p.all_tokens)
                if len(exact) != 1:
                    session.last_product = None
                    return self._clarify(hits)
                pick = exact[0]
            if pick is not None:
                session.last_product = pick.id
                return self._product_answer(pick, p, lead=self.SORT_LABELS.get(spec.sort))
        elif specific:
            session.last_product = hits[0].id
            return self._product_answer(hits[0], p)

        session.last_product = hits[0].id if len(hits) == 1 else None
        return self._listing(hits, p, spec, relaxed)

    def _resolve(self, p: Parsed, session: Session) -> tuple[Product | None, Spec]:
        """Decide whether the turn is about one known product, a new search, or a refinement."""
        s = p.spec
        if p.product_id:
            return self.index.products[p.product_id], Spec()
        asks_fact = bool(p.intents & {"price", "stock", "rating", "detail"})
        no_ref = not (s.type_tokens or s.filters or s.size or s.price_min is not None or s.price_max is not None)
        if session.last_product and no_ref and (asks_fact or p.unknown or s.desc_tokens):
            return self.index.products[session.last_product], Spec()
        refining = not s.type_tokens and not session.spec.is_empty() and (
            p.has_constraint or s.sort or s.desc_tokens)
        if not refining:
            return None, s.copy()
        merged = session.spec.copy()
        merged.filters.update(s.filters)
        merged.desc_tokens = list(dict.fromkeys(merged.desc_tokens + s.desc_tokens))
        for attr in ("price_min", "price_max", "size", "sort"):
            if getattr(s, attr) is not None:
                setattr(merged, attr, getattr(s, attr))
        return None, merged

    # -- answer builders -----------------------------------------------------------------------
    def _refuse(self, text: str) -> Response:
        return Response(text, REFUSE)

    def _fact_claims(self, p: Product) -> list[dict]:
        claims = [{"pid": p.id, "field": "price", "value": p.price},
                  {"pid": p.id, "field": "in_stock", "value": p.in_stock}]
        if p.rating is not None:
            claims.append({"pid": p.id, "field": "rating", "value": p.rating})
        return claims

    @staticmethod
    def _stock_phrase(p: Product) -> str:
        return "in stock" if p.in_stock else "out of stock"

    def _product_answer(self, prod: Product, p: Parsed, lead: str | None = None) -> Response:
        cite = f"[{prod.id}]"
        want = p.intents & {"price", "stock", "rating", "detail"}
        sentences, claims, supported = [], [], False
        named = False

        def subject() -> str:
            nonlocal named
            if named:
                return "It"
            named = True
            return f"The {prod.title}"

        if lead:
            sentences.append(f"{subject()} is the {lead} match {cite}.")
        if "price" in want or not want:
            sentences.append(f"{subject()} is priced at {format_inr(prod.price)} {cite}.")
            claims.append({"pid": prod.id, "field": "price", "value": prod.price})
            supported = True
        if "stock" in want or not want:
            sentences.append(f"{subject()} is {self._stock_phrase(prod)} {cite}.")
            claims.append({"pid": prod.id, "field": "in_stock", "value": prod.in_stock})
            supported = True
        if ("rating" in want or not want) and prod.rating is not None:
            count = f" from {prod.rating_count} ratings" if prod.rating_count else ""
            sentences.append(f"{subject()} is rated {prod.rating} stars{count} {cite}.")
            claims.append({"pid": prod.id, "field": "rating", "value": prod.rating})
            supported = True
        elif "rating" in want:
            sentences.append(f"The catalogue has no rating for the {prod.title} {cite}.")
        for key in p.detail_keys:
            values = prod.attr_values(key)
            if values:
                sentences.append(f"The {key} of the {prod.title} is {', '.join(values)} {cite}.")
                claims.append({"pid": prod.id, "field": key, "value": values})
                supported = True
            else:
                sentences.append(f"The catalogue does not list a {key} for the {prod.title} {cite}.")
        if "detail" in want or p.spec.desc_tokens or p.unknown:
            extra, ok = self._description_check(prod, p)
            sentences.extend(extra)
            supported = supported or ok
        return Response(" ".join(sentences), ANSWER if supported else REFUSE, claims)

    def _description_check(self, prod: Product, p: Parsed) -> tuple[list[str], bool]:
        out, ok = [], False
        desc_sents = split_sentences(prod.description)
        for tok in dict.fromkeys(p.spec.desc_tokens + p.unknown):
            hit = next((s for s in desc_sents if tok in tokenize(s)), None)
            if hit:
                out.append(f'The description says: "{hit}" [{prod.id}]')
                ok = True
            else:
                out.append(f"I don't have information about \"{tok}\" for the {prod.title} [{prod.id}].")
        return out, ok

    def _listing(self, hits: list[Product], p: Parsed, spec: Spec, relaxed: bool) -> Response:
        shown = hits[: self.list_limit]
        header = "Here are the best matches:"
        if spec.sort == "price_asc":
            header = "Here are the lowest-priced matches:"
        elif spec.sort == "rating_desc":
            header = "Here are the highest-rated matches:"
        lines, claims = [header], []
        for h in shown:
            rating = f", rated {h.rating} stars" if h.rating is not None else ""
            lines.append(f"- {h.title}: {format_inr(h.price)}{rating}, {self._stock_phrase(h)} [{h.id}]")
            claims.extend(self._fact_claims(h))
        if len(hits) > len(shown):
            lines.append("There are more matches; tell me a brand, colour or budget to narrow them down.")
        if relaxed and spec.desc_tokens:
            lines.append(f"I could not confirm \"{' '.join(spec.desc_tokens)}\" in the product descriptions, "
                         "so these are the closest matches.")
        if p.unknown:
            lines.append(f"I can't verify \"{' '.join(p.unknown)}\" from the catalogue.")
        return Response("\n".join(lines), ANSWER, claims)

    def _clarify(self, hits: list[Product]) -> Response:
        keys = []
        for h in hits:
            for k in h.attributes:
                if k.lower() not in SIZE_KEYS and k not in keys:
                    keys.append(k)
        varying = [k for k in keys if len({tuple(h.attr_values(k)) for h in hits}) > 1][:3]
        lines = ["I found more than one match, so I don't want to guess. Which one do you mean?"]
        claims = []
        for h in hits[: self.clarify_limit]:
            detail = ", ".join(v for k in varying for v in h.attr_values(k)[:1])
            lines.append(f"- {h.title}{f' ({detail})' if detail else ''} [{h.id}]")
            claims += [{"pid": h.id, "field": k, "value": h.attr_values(k)} for k in varying]
        if len(hits) > self.clarify_limit:
            lines.append("There are more options too.")
        if varying:
            lines.append("You can tell me the " + " or ".join(varying) + " to pick one.")
        return Response("\n".join(lines), CLARIFY, claims)

    def _no_match(self, spec: Spec, p: Parsed) -> Response:
        base = "I don't have that in the catalogue."
        if spec.price_max is not None or spec.price_min is not None:
            looser = spec.copy()
            looser.price_min = looser.price_max = None
            alt, _ = self.index.search(looser, p.all_tokens)
            if alt:
                pick = min(alt, key=lambda x: x.price) if spec.price_max is not None else max(alt, key=lambda x: x.price)
                word = "lowest" if spec.price_max is not None else "highest"
                return Response(f"{base} The {word}-priced option that matches otherwise is {pick.title} at "
                                f"{format_inr(pick.price)} [{pick.id}].", REFUSE, self._fact_claims(pick))
        if spec.filters or spec.size:
            looser = spec.copy()
            looser.filters, looser.size = {}, None
            alt, _ = self.index.search(looser, p.all_tokens)
            if alt:
                shown = alt[: self.list_limit]
                lines = [f"{base} Similar products without that exact combination:"]
                lines += [f"- {h.title} [{h.id}]" for h in shown]
                return Response("\n".join(lines), REFUSE)
        return Response(base, REFUSE)

    def _policy(self, p: Parsed) -> Response:
        found = best_sentences(self.policy, p.keywords)
        if not found:
            return self._refuse("I don't have that in our policy information.")
        sec, sentences = found
        return Response(" ".join(f"{s} [policy:{sec.id}]" for s in sentences), ANSWER,
                        [{"policy": sec.id, "text": s} for s in sentences])
