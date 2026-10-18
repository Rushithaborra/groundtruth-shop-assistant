"""The grounded shopping assistant.

Every sentence is assembled from catalogue fields or extracted from the policy text, and carries a citation
([P0001] for a product, [policy:returns] for a policy section). Nothing is generated freely, so a price that
is not in the catalogue cannot be produced. When the catalogue cannot support an answer it refuses; when
several products fit a question about "the" product it asks which one.

Thread safety: the assistant and its index are read-only after construction, so one instance can serve all
threads. All per-user state lives in a `Session`, which callers keep per user (see `SessionStore`).
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from .catalogue import Product
from .index import SIZE_KEY, CatalogueIndex, Spec
from .policy import Section, best_sentences
from .text import EXTERNAL_DISPLAY, approx_tokens, format_inr
from .understand import Parsed, understand

ANSWER, CLARIFY, REFUSE = "answer", "clarify", "refuse"
CITATION = re.compile(r"\[([^\]]+)\]")
SORT_LABELS = {"price_asc": "lowest-priced", "price_desc": "highest-priced", "rating_desc": "highest-rated"}
FACTS = frozenset({"price", "stock", "rating", "detail"})


@dataclass
class Response:
    text: str
    action: str
    latency_ms: float = 0.0
    context_tokens: int = 0
    output_tokens: int = 0
    path: str = "rules"        # rules | semantic | llm: which understanding path resolved the query
    llm_input_tokens: int = 0  # real tokens sent to / produced by the instruct model (0 on other paths)
    llm_output_tokens: int = 0

    @property
    def citations(self) -> list[str]:
        return list(dict.fromkeys(CITATION.findall(self.text)))


@dataclass
class Session:
    spec: Spec = field(default_factory=Spec)
    last_product: str | None = None

    def clear(self) -> None:
        self.spec, self.last_product = Spec(), None


def _stock(p: Product) -> str:
    if p.in_stock is None:
        return "stock status not listed"
    return "in stock" if p.in_stock else "out of stock"


def _line(p: Product, price=True, rating=True, stock=True) -> str:
    """One cited catalogue line, e.g. '- Title: ₹1,299, rated 4.1 stars, in stock [P0001]'."""
    parts = [format_inr(p.price)] if price else []
    if rating and p.rating is not None:
        parts.append(f"rated {p.rating} stars")
    if stock:
        parts.append(_stock(p))
    return f"- {p.title}: {', '.join(parts)} [{p.id}]"


class ShoppingAssistant:
    def __init__(self, index: CatalogueIndex, policy: list[Section], list_limit: int = 3, clarify_limit: int = 5,
                 measure_tokens: bool = False, semantic=None, normaliser=None):
        self.index, self.policy = index, policy
        self.semantic = semantic      # optional SemanticFallback (embedding model), miss path only
        self.normaliser = normaliser  # optional LLMNormaliser (instruct model), last resort on the miss path
        self.sections = {s.id: s for s in policy}
        self.list_limit, self.clarify_limit = list_limit, clarify_limit
        self.measure_tokens = measure_tokens  # token estimates cost time; enable for reports, not for serving

    # -- public --------------------------------------------------------------------------------
    def warm_up(self) -> None:
        """Run a few queries once so first real requests don't pay for lazy initialisation."""
        for query in ("hi", "cheap runing shoes under 2k", "return policy", "price of xyzzy"):
            self.ask(query)

    def ask(self, query: str, session: Session | None = None) -> Response:
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
        if "greet" in p.intents:
            return Response("Hi! I can help you find products, check price, stock and ratings, and answer "
                            "questions about returns, shipping and warranty. What do you need?", CLARIFY)
        if "policy" in p.intents:
            if p.external and not (p.product_id or p.mentioned):  # "warranty on samsung tv"
                return self._external_policy(p)
            return self._policy(p)
        resp = self._product_route(p, session)
        if "policy_q" in p.intents and resp.action != CLARIFY:  # "price of X and what is the return policy"
            pol = self._policy(p)
            if pol.action == ANSWER:
                return Response(f"{resp.text}\n{pol.text}", ANSWER) if resp.action == ANSWER else pol
        return resp

    def _product_route(self, p: Parsed, session: Session) -> Response:
        if len(p.mentioned) >= 2 and not p.product_id:
            return self._compare(p.mentioned, p)
        if p.external and not (p.product_id or p.mentioned):
            return self._external(p)
        forced, spec = self._resolve(p, session)
        if forced is not None:
            session.last_product = forced.id
            return self._product_answer(forced, p)

        if not spec.type_tokens and not spec.filters and spec.desc_tokens and session.spec.is_empty():
            # only description words (e.g. "laptop" appearing in a backpack's text): not a product the shop sells
            p.unknown = p.unknown + spec.desc_tokens
            p.spec.desc_tokens = spec.desc_tokens = []
        known = len(p.spec.type_tokens) + len(p.spec.desc_tokens) + len(p.spec.filters)
        names_product = bool(p.spec.type_tokens)  # "sneakers for gym": shoes are sold, gym is a modifier; a colour alone is not
        if p.unknown and not names_product and len(p.unknown) >= max(known, 1):
            if self.semantic is not None and not p.external:  # "money back timeline": a policy question in other words
                hit = self.semantic.policy_sentence(p.raw)
                if hit:
                    sec, sentence, _ = hit
                    return Response(f"{sentence} [policy:{sec.id}]", ANSWER, path="semantic")
            return self._unknown_refusal(p)
        if spec.is_empty():
            return Response("What are you looking for? Tell me the kind of product, and a brand, colour or budget "
                            "if you have one.", CLARIFY)

        hits, relaxed = self.index.search(spec, p.all_tokens)
        if not hits:
            return self._no_match(spec, p)
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
                    return self._clarify(hits)
                pick = exact[0]
        if pick is not None:
            session.last_product = pick.id
            return self._product_answer(pick, p, lead=lead)

        session.last_product = hits[0].id if len(hits) == 1 else None
        return self._listing(hits, p, spec, relaxed)

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

    # -- answer builders -----------------------------------------------------------------------
    def _product_answer(self, prod: Product, p: Parsed, lead: str | None = None) -> Response:
        cite = f"[{prod.id}]"
        want = p.intents & FACTS
        # catalogue words that don't describe this product ("does <kettle> have bluetooth") are questions about it
        foreign = [t for t in p.spec.type_tokens if not self.index.has_text_token(prod.id, t)]
        facts_only = not want and bool(p.spec.desc_tokens or p.unknown or foreign)  # "is X waterproof?"
        defaults = not want and not facts_only
        sentences, supported, named = [], False, False

        def say(predicate: str) -> None:
            nonlocal named
            sentences.append(f"{'It' if named else f'The {prod.title}'} {predicate} {cite}.")
            named = True

        if lead:
            say(f"is the {lead} match")
        if "price" in want or defaults:
            say(f"is priced at {format_inr(prod.price)}")
            supported = True
        if "stock" in want or defaults:
            say(f"is {_stock(prod)}" if prod.in_stock is not None else "has no stock status in the catalogue")
            supported = True
        if "rating" in want or defaults:
            if prod.rating is not None:
                count = f" from {prod.rating_count} ratings" if prod.rating_count else ""
                say(f"is rated {prod.rating} stars{count}")
                supported = True
            elif "rating" in want:
                sentences.append(f"The catalogue has no rating for the {prod.title} {cite}.")
        for key in p.detail_keys:
            values = prod.attr_values(key)
            if values:
                sentences.append(f"The {key} of the {prod.title} is {', '.join(values)} {cite}.")
                supported = True
            else:
                sentences.append(f"The catalogue does not list a {key} for the {prod.title} {cite}.")
        if "detail" in want or p.spec.desc_tokens or p.unknown or foreign:
            extra, ok = self._description_check(prod, p, foreign)
            sentences += extra
            supported = supported or ok
        return Response(" ".join(sentences), ANSWER if supported else REFUSE)

    def _compare(self, pids: list[str], p: Parsed) -> Response:
        prods = [self.index.products[i] for i in pids][:4]
        want = (p.intents & {"price", "stock", "rating"}) or {"price"}
        lines = ["Here is the comparison:"]
        lines += [_line(x, "price" in want, "rating" in want, "stock" in want) for x in prods]
        sort = p.spec.sort
        if sort in ("price_asc", "price_desc"):
            pick = (min if sort == "price_asc" else max)(prods, key=lambda x: x.price)
            if any(x.price != pick.price for x in prods):
                word = "cheaper" if sort == "price_asc" else "more expensive"
                lines.append(f"The {pick.title} is the {word} one at {format_inr(pick.price)} [{pick.id}].")
            else:
                lines.append(f"They are the same price, {format_inr(pick.price)} " + " ".join(f"[{x.id}]" for x in prods) + ".")
        elif sort == "rating_desc" and (rated := [x for x in prods if x.rating is not None]):
            pick = max(rated, key=lambda x: x.rating)
            lines.append(f"The {pick.title} has the higher rating, {pick.rating} stars [{pick.id}].")
        return Response("\n".join(lines), ANSWER)

    def _description_check(self, prod: Product, p: Parsed, foreign: list[str] = ()) -> tuple[list[str], bool]:
        out, ok = [], False
        in_description = self.index.desc_tokens[prod.id]
        for tok in dict.fromkeys(p.spec.desc_tokens + p.unknown + list(foreign)):
            if tok in in_description:
                sentence = next((s for s in re.split(r"(?<=[.!?])\s+", prod.description) if tok in s.lower()),
                                prod.description)
                out.append(f'The description says: "{sentence}" [{prod.id}]')
                ok = True
            else:
                out.append(f"I don't have information about \"{tok}\" for the {prod.title} [{prod.id}].")
        return out, ok

    def _listing(self, hits: list[Product], p: Parsed, spec: Spec, relaxed: bool) -> Response:
        shown = hits[: self.list_limit]
        kind = {"price_asc": "lowest-priced", "rating_desc": "highest-rated"}.get(spec.sort, "best")
        lines = [f"Here are the {kind} matches:"] + [_line(h) for h in shown]
        if len(hits) > len(shown):
            lines.append("There are more matches; tell me a brand, colour or budget to narrow them down.")
        if p.mapped:
            lines.insert(0, "(I read " + ", ".join(f'"{w}" as "{t}"' for w, t in p.mapped) + ".)")
        if relaxed and spec.desc_tokens:
            lines.append(f"I could not confirm \"{' '.join(spec.desc_tokens)}\" in the product descriptions, "
                         "so these are the closest matches.")
        if p.unknown:
            lines.append(f"I can't verify \"{' '.join(p.unknown)}\" from the catalogue.")
        return Response("\n".join(lines), ANSWER)

    def _clarify(self, hits: list[Product]) -> Response:
        lowered = self.index.attr_lower
        keys = dict.fromkeys(k for h in hits for k in lowered[h.id] if k != SIZE_KEY)
        varying = [k for k in keys if len({lowered[h.id].get(k) for h in hits}) > 1][:3]
        lines = ["I found more than one match, so I don't want to guess. Which one do you mean?"]
        for h in hits[: self.clarify_limit]:
            detail = ", ".join(v for k in varying for v in h.attr_values(k)[:1])
            lines.append(f"- {h.title}{f' ({detail})' if detail else ''} [{h.id}]")
        if len(hits) > self.clarify_limit:
            lines.append("There are more options too.")
        if varying:
            lines.append("You can tell me the " + " or ".join(varying) + " to pick one.")
        return Response("\n".join(lines), CLARIFY)

    def _no_match(self, spec: Spec, p: Parsed) -> Response:
        base = "I don't have that in the catalogue."
        if spec.price_max is not None or spec.price_min is not None:
            looser = spec.copy()
            looser.price_min = looser.price_max = None
            alt, _ = self.index.search(looser, p.all_tokens)
            if alt:
                lowest = spec.price_max is not None
                pick = (min if lowest else max)(alt, key=lambda x: x.price)
                return Response(f"{base} The {'lowest' if lowest else 'highest'}-priced option that matches otherwise "
                                f"is {pick.title} at {format_inr(pick.price)} [{pick.id}].", REFUSE)
        if spec.filters or spec.size:
            looser = spec.copy()
            looser.filters, looser.size = {}, None
            alt, _ = self.index.search(looser, p.all_tokens)
            if alt:
                lines = [f"{base} Similar products without that exact combination:"]
                lines += [f"- {h.title} [{h.id}]" for h in alt[: self.list_limit]]
                return Response("\n".join(lines), REFUSE)
        return Response(base, REFUSE)

    def _alternatives(self, spec: Spec, p: Parsed, header: str) -> list[str]:
        hits, _ = self.index.search(spec, p.all_tokens)
        if not hits:
            return []
        return [header] + [_line(h) for h in hits[: self.list_limit]]

    def _external(self, p: Parsed) -> Response:
        names = " ".join(EXTERNAL_DISPLAY.get(w, w.capitalize()) for w in dict.fromkeys(p.external))
        lines = [f"I don't have {names} products in the catalogue."]
        if p.spec.type_tokens:
            other = p.spec.copy()
            other.desc_tokens = []
            lines += self._alternatives(other, p, "Similar products from brands I do carry:")
        return Response("\n".join(lines), REFUSE)

    def _external_policy(self, p: Parsed) -> Response:
        names = " ".join(EXTERNAL_DISPLAY.get(w, w.capitalize()) for w in dict.fromkeys(p.external))
        text = f"I don't have {names} products in the catalogue, so I can't say what applies to one."
        pol = self._policy(p)
        if pol.action == ANSWER:
            text += f" The general policy is: {pol.text}"
        return Response(text, REFUSE)

    def _unknown_refusal(self, p: Parsed) -> Response:
        lines = [f"I don't have anything in the catalogue matching \"{' '.join(p.unknown)}\"."]
        for alt in dict.fromkeys(p.alternatives):
            lines += self._alternatives(Spec(type_tokens=[alt]), p, "The closest products I do have:")
            break
        return Response("\n".join(lines), REFUSE)

    def _policy(self, p: Parsed) -> Response:
        found = best_sentences(self.policy, p.keywords)
        if not found and self.semantic is not None:
            hit = self.semantic.policy_sentence(p.raw)
            if hit:
                sec, sentence, _ = hit
                return Response(f"{sentence} [policy:{sec.id}]", ANSWER, path="semantic")
        if not found:
            return Response("I don't have that in our policy information.", REFUSE)
        sec, sentences = found
        return Response(" ".join(f"{s} [policy:{sec.id}]" for s in sentences), ANSWER)
