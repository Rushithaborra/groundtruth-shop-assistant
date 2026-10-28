"""The grounded shopping assistant.

Every sentence is assembled from catalogue fields or extracted from the policy text, and carries a citation
([P0001] for a product, [policy:returns] for a policy section). Nothing is generated freely, so a price that
is not in the catalogue cannot be produced. When the catalogue cannot support an answer it refuses; when
several products fit a question about "the" product it asks which one.

Thread safety: the assistant and its index are read-only after construction, so one instance can serve all
threads. All per-user state lives in a `Session`, which callers keep per user (see `SessionStore`).
"""
from __future__ import annotations

import time

from ..catalogue import CatalogueIndex, Product, Spec
from ..config import get_settings
from ..policy import Section
from ..text import approx_tokens, format_inr
from ..understanding import Parsed, understand
from . import listings, product_answers, refusals
from .formatting import SORT_LABELS, head_nouns
from .policy_answers import policy_answer
from .product_answers import FACTS
from .response import ANSWER, CLARIFY, REFUSE, Response, Session


class ShoppingAssistant:
    def __init__(self, index: CatalogueIndex, policy: list[Section], list_limit: int | None = None,
                 clarify_limit: int | None = None, measure_tokens: bool = False, semantic=None, normaliser=None):
        limits = get_settings().assistant
        self.index, self.policy = index, policy
        self.semantic = semantic      # optional SemanticFallback (embedding model), miss path only
        self.head_nouns = head_nouns(index)
        self.normaliser = normaliser  # optional LLMNormaliser (instruct model), last resort on the miss path
        self.sections = {s.id: s for s in policy}
        self.list_limit = list_limit if list_limit is not None else limits.list_limit
        self.clarify_limit = clarify_limit if clarify_limit is not None else limits.clarify_limit
        self.policy_max_sentences = limits.policy_max_sentences
        self.measure_tokens = measure_tokens  # token estimates cost time; enable for reports, not for serving

    # -- public --------------------------------------------------------------------------------
    def warm_up(self) -> None:
        """Run a few queries once so first real requests don't pay for lazy initialisation."""
        for query in ("hi", "cheap runing shoes under 2k", "return policy", "price of xyzzy"):
            self.ask(query)

    def ask(self, query: str, session: Session | None = None) -> Response:
        """Answer one shopper message; `session` carries filters and the last product between turns."""
        session = session if session is not None else Session()
        start = time.perf_counter()
        parsed, path, llm_usage = understand(query, self.index), "rules", (0, 0)
        if self._missed(parsed) and self.semantic is not None and self.semantic.resolve(parsed):
            path = "semantic"
        if self._missed(parsed) and self.normaliser is not None:
            llm_usage = self.normaliser.apply(parsed, self.index)
            if llm_usage != (0, 0):
                path = "llm"
        resp = self._route(parsed, session)
        if resp.path == "rules":  # a policy answer found by the embedding fallback has already marked itself
            resp.path = path
        resp.llm_input_tokens, resp.llm_output_tokens = llm_usage
        resp.latency_ms = (time.perf_counter() - start) * 1000
        if self.measure_tokens:
            resp.output_tokens = approx_tokens(resp.text)
            resp.context_tokens = approx_tokens(query) + sum(approx_tokens(self._source_text(c)) for c in resp.citations)
        return resp

    @staticmethod
    def _missed(p: Parsed) -> bool:
        """The rule-based parser left product words it couldn't place (and it isn't a brand we know we lack)."""
        return bool(set(p.unknown) - set(p.external)) and not p.product_id and not p.mentioned

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
        if "thanks" in p.intents:
            return Response("You're welcome! Ask me about another product, a price or a store policy whenever you like.",
                            CLARIFY)
        if "account" in p.intents:
            return Response("I can't see orders, accounts, payments, stores or offers. I can only answer from the product "
                            "catalogue and the store's returns, exchange, shipping, cancellation and warranty policies.",
                            REFUSE)
        if "greet" in p.intents:
            return Response("Hi! I can help you find products, check price, stock and ratings, and answer "
                            "questions about returns, shipping and warranty. What do you need?", CLARIFY)
        if "policy" in p.intents:
            if p.external and not (p.product_id or p.mentioned):  # "warranty on samsung tv"
                return refusals.external_policy(p, self._policy(p))
            return self._policy(p)
        resp = self._product_route(p, session)
        if "policy_q" in p.intents and resp.action != CLARIFY:  # "price of X and what is the return policy"
            pol = self._policy(p)
            if pol.action == ANSWER:
                return Response(f"{resp.text}\n{pol.text}", ANSWER) if resp.action == ANSWER else pol
        return resp

    def _policy(self, p: Parsed) -> Response:
        return policy_answer(self.policy, self.semantic, p, self.policy_max_sentences)

    def _product_route(self, p: Parsed, session: Session) -> Response:
        if len(p.mentioned) >= 2 and not p.product_id:
            return product_answers.compare(self.index, p.mentioned, p)
        if p.external and not (p.product_id or p.mentioned):
            return refusals.external(self.index, p, self.list_limit)
        forced, spec = self._resolve(p, session)
        if forced is not None:
            session.last_product = forced.id
            return product_answers.product_answer(self.index, forced, p)

        if not spec.type_tokens and not spec.filters and spec.desc_tokens and session.spec.is_empty():
            # only description words (e.g. "laptop" appearing in a backpack's text): not a product the shop sells
            p.unknown = p.unknown + spec.desc_tokens
            p.spec.desc_tokens = spec.desc_tokens = []
        known = len(p.spec.type_tokens) + len(p.spec.desc_tokens) + len(p.spec.filters)
        # "sneakers for gym": shoes are sold, gym is a modifier. But a describing word alone ("wireless mouse",
        # "electric scooter") doesn't name a product we sell, and neither does a colour.
        # (a fallback that already identified the product type counts as naming it)
        names_product = any(t in self.head_nouns for t in p.spec.type_tokens) or bool(p.mapped)
        if p.unknown and not names_product and len(p.unknown) >= max(known, 1):
            if self.semantic is not None and not p.external:  # "money back timeline": a policy question in other words
                hit = self.semantic.policy_sentence(p.raw)
                if hit:
                    sec, sentence, _ = hit
                    return Response(f"{sentence} [policy:{sec.id}]", ANSWER, path="semantic")
            return refusals.unknown_refusal(self.index, p, self.list_limit)
        only_exclusions = not (spec.type_tokens or spec.filters or spec.desc_tokens or spec.size or spec.sort
                               or spec.price_min is not None or spec.price_max is not None)
        if (spec.is_empty() or only_exclusions) and not spec.sort:  # "not nike" alone says what isn't wanted
            return Response("What are you looking for? Tell me the kind of product, and a brand, colour or budget "
                            "if you have one.", CLARIFY)

        unlisted = _drop_unlisted_filters(self.index, spec, p)
        hits, relaxed = self.index.search(spec, p.all_tokens)
        if not hits:
            return refusals.no_match(self.index, spec, p, self.list_limit)
        new_words = p.spec.desc_tokens
        only_words = not (p.spec.filters or p.spec.size or p.spec.sort or p.spec.price_min is not None
                          or p.spec.price_max is not None)
        if p.refined and relaxed and new_words and only_words and \
                not any(t in self.index.desc_tokens[h.id] for h in hits for t in new_words):
            # "laptop" after "black hoodie": none of the products being discussed match, so don't re-show them
            return Response(f"I don't have anything matching \"{' '.join(new_words)}\" among the products we were "
                            "looking at. Tell me what you'd like instead, or say \"start over\".", REFUSE)
        session.spec = spec

        specific = p.intents & {"price", "rating", "detail"}
        if "stock" in p.intents and {"the", "this", "that"} & p.all_tokens:  # "is the grey backpack in stock"
            specific = specific | {"stock"}
        lead = SORT_LABELS.get(spec.sort)
        pick = None
        if len(hits) == 1 and specific:
            pick = hits[0]
        elif len(hits) > 1 and spec.sort and not p.spec.type_tokens and not specific and {"the", "one"} & p.all_tokens:
            pick = hits[0]  # "the cheapest" after a search
        elif len(hits) > 1 and specific:
            if spec.sort:
                pick = hits[0]
            elif spec.price_max is None and spec.price_min is None:  # a budget implies browsing, not one product
                exact = self.index.exact_title_matches(hits, p.all_tokens)
                if len(exact) != 1:
                    session.last_product = None
                    return listings.clarify(self.index, hits, self.clarify_limit)
                pick = exact[0]
        if pick is not None:
            session.last_product = pick.id
            return product_answers.product_answer(self.index, pick, p, lead=lead)

        session.last_product = hits[0].id if len(hits) == 1 else None
        resp = listings.listing(hits, p, spec, relaxed, self.list_limit)
        if unlisted:
            resp.text += (f"\n(The catalogue doesn't list {' or '.join(unlisted)} for these products, so I couldn't "
                          "filter by it.)")
        return resp

    def _resolve(self, p: Parsed, session: Session) -> tuple[Product | None, Spec]:
        """Decide whether the turn is about one known product, a new search, or a refinement."""
        s, products = p.spec, self.index.products
        if p.product_id:
            return products[p.product_id], Spec()
        if len(p.mentioned) == 1:
            return products[p.mentioned[0]], Spec()
        no_ref = not (s.type_tokens or s.filters or s.size or s.price_min is not None or s.price_max is not None)
        if session.last_product and no_ref and (p.intents & FACTS or p.unknown or s.desc_tokens):
            return products[session.last_product], Spec()
        if s.type_tokens or session.spec.is_empty() or not (p.has_constraint or s.sort or s.desc_tokens):
            return None, s.copy()
        merged = session.spec.copy()  # a refinement of the previous search
        merged.filters.update(s.filters)
        for key, values in s.exclude.items():
            merged.exclude[key] = list(dict.fromkeys(merged.exclude.get(key, []) + values))
        merged.desc_tokens = list(dict.fromkeys(merged.desc_tokens + s.desc_tokens))
        for attr in ("price_min", "price_max", "size", "sort"):
            if getattr(s, attr) is not None:
                setattr(merged, attr, getattr(s, attr))
        if s.size is not None:
            merged.size_soft = s.size_soft
        p.refined = True
        return None, merged


def _drop_unlisted_filters(index: CatalogueIndex, spec: Spec, p: Parsed) -> list[str]:
    """Drop filters on an attribute that none of the requested products has (\"smartwatch for women\" when
    smartwatches list no gender): filtering by it would wrongly refuse. Returns the dropped attribute names."""
    if not (spec.filters and spec.type_tokens):
        return []
    base = spec.copy()
    base.filters, base.price_min, base.price_max, base.size = {}, None, None, None
    pool, _ = index.search(base, p.all_tokens)
    dropped = [k for k in spec.filters if pool and not any(x.attr_values(k) for x in pool)]
    for k in dropped:
        del spec.filters[k]
    return dropped
