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
from typing import TYPE_CHECKING, Callable

from ..catalogue import KIND_KEY, CatalogueIndex, Product, Spec
from ..concurrency import ReadWriteLock
from ..config import get_settings
from ..policy import STRONG, Section
from ..text import (
    GENERAL_ASK_WORDS,
    GIFT_SERVICES,
    GIFT_WORDS,
    NEEDS,
    POLICY_TIME_WORDS,
    UNSOLD_PRODUCTS,
    approx_tokens,
    field_label,
    format_inr,
    tokenize,
)
from ..understanding import Parsed, understand
from ..understanding.lexicon import BROWSE_W
from ..understanding.parser import COMMON_WORD_PRODUCTS
from . import listings, product_answers, refusals
from .formatting import SORT_LABELS, head_nouns, product_line
from .policy_answers import cited_section, policy_answer, policy_silent
from .product_answers import FACTS
from .response import ANSWER, CLARIFY, REFUSE, Claims, Response, Session, spec_from_dict, spec_to_dict

if TYPE_CHECKING:  # only for the annotations below: the model fallbacks are optional and load lazily
    from ..models.normaliser import LLMNormaliser
    from ..models.semantic import SemanticFallback
    from ..synonyms import LearnedSynonyms


class ShoppingAssistant:
    """Answers one shopper message at a time from the catalogue and the policy only: it answers with citations,
    asks which one is meant, or says the catalogue doesn't have it."""

    def __init__(
        self,
        index: CatalogueIndex,
        policy: list[Section],
        list_limit: int | None = None,
        clarify_limit: int | None = None,
        measure_tokens: bool = False,
        semantic: SemanticFallback | None = None,
        normaliser: LLMNormaliser | None = None,
        synonyms: LearnedSynonyms | None = None,
    ) -> None:
        limits = get_settings().assistant
        self.index, self.policy = index, policy
        self.semantic = semantic  # optional SemanticFallback (embedding model), miss path only
        self.head_nouns = head_nouns(index)
        self.normaliser = normaliser  # optional LLMNormaliser (instruct model), last resort on the miss path
        self.synonyms = synonyms  # optional LearnedSynonyms: words a person approved, answered with no model
        self.sections = {s.id: s for s in policy}
        self.list_limit = list_limit if list_limit is not None else limits.list_limit
        self.clarify_limit = clarify_limit if clarify_limit is not None else limits.clarify_limit
        self.policy_max_sentences = limits.policy_max_sentences
        # the policy's named terms ("GST", "COD"): one alone is enough to identify a policy question
        self.policy_words = STRONG | _policy_terms(policy, index)
        self.policy_vocab = frozenset(t for sec in policy for _, toks in sec.sentences for t in toks)
        self._catalogue_lock = ReadWriteLock()  # questions read the index; apply_updates changes it
        self.measure_tokens = measure_tokens  # token estimates cost time; enable for reports, not for serving

    # -- public --------------------------------------------------------------------------------
    def warm_up(self) -> None:
        """Run a few queries once so first real requests don't pay for lazy initialisation."""
        self.index.prepare()
        for query in ("hi", "cheap runing shoes under 2k", "return policy", "price of xyzzy"):
            self.ask(query)

    def apply_updates(
        self,
        add: list[Product] | None = None,
        remove: list[str] | None = None,
        price_stock: list[dict] | None = None,
    ) -> dict[str, int]:
        """Change the live catalogue without a restart or a rebuild: `price_stock` is a list of {"id", "price"?,
        "in_stock"?, "stock_status"?}; `add` replaces products with the same id. Questions wait while it runs."""
        done = {"added": 0, "removed": 0, "price_stock": 0}
        with self._catalogue_lock.writing():
            for change in price_stock or []:
                fields = {k: v for k, v in change.items() if k != "id"}
                done["price_stock"] += self.index.update_price_stock(change["id"], **fields)
            done["removed"] = sum(self.index.remove_product(pid) for pid in remove or [])
            for product in add or []:
                self.index.add_product(product)
                done["added"] += 1
            if add or remove:
                self.index.refresh()
                self.head_nouns = head_nouns(self.index)
        return done

    def ask(self, query: str, session: Session | None = None) -> Response:
        """Answer one shopper message; `session` carries filters and the last product between turns."""
        with self._catalogue_lock.reading():
            return self._ask(query, session)

    def _ask(self, query: str, session: Session | None) -> Response:
        session = session if session is not None else Session()
        session.carry_from = None  # set again by _resolve when this turn moves to another product
        start = time.perf_counter()
        parsed, path, llm_usage = understand(query, self.index), "rules", (0, 0)
        if self._missed(parsed) and self.synonyms is not None and self.synonyms.apply(parsed, self.index):
            path = "synonym"  # an approved mapping: no model, no tokens
        mapped_before = len(parsed.mapped)
        if self._missed(parsed) and self.semantic is not None and self.semantic.resolve(parsed):
            path = "semantic"
        if self._missed(parsed) and self.normaliser is not None:
            llm_usage = self.normaliser.apply(parsed, self.index)
            if llm_usage != (0, 0):
                path = "llm"
        self._learn(parsed, mapped_before, path)
        resp = self._route(parsed, session)
        if parsed.corrected and resp.action != CLARIFY:  # a mended short word is said aloud, so a wrong guess shows
            fixes = ", ".join(f'"{shown}" (you typed "{typed}")' for typed, shown in parsed.corrected)
            resp.text = f"Showing results for {fixes}.\n{resp.text}"
        if resp.path == "rules":  # a policy answer found by the embedding fallback has already marked itself
            resp.path = path
        resp.llm_input_tokens, resp.llm_output_tokens = llm_usage
        resp.latency_ms = (time.perf_counter() - start) * 1000
        if self.measure_tokens:
            resp.output_tokens = approx_tokens(resp.text)
            resp.context_tokens = approx_tokens(query) + sum(
                approx_tokens(self._source_text(c)) for c in resp.citations
            )
        return resp

    def _learn(self, parsed: Parsed, since: int, path: str) -> None:
        """Hand the words a model just resolved to the synonym table as proposals for a person to review."""
        if self.synonyms is not None and path in ("semantic", "llm"):
            for word, target in parsed.mapped[since:]:
                self.synonyms.propose(word, target, path)

    @staticmethod
    def _missed(p: Parsed) -> bool:
        """The rule-based parser left product words it couldn't place (and it isn't a brand we know we lack)."""
        return bool(set(p.unknown) - set(p.external)) and not p.product_id and not p.mentioned

    def _source_text(self, citation: str) -> str:
        """The text a citation stands for, used to estimate how much context a reply cites."""
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
        """Pick the kind of reply: reset, thanks, account (refused), greeting, policy, or a product answer."""
        if "reset" in p.intents:
            session.clear()
            return Response("Okay, starting fresh. What are you looking for?", CLARIFY)
        if "thanks" in p.intents:
            return Response(
                "You're welcome! Ask me about another product, a price or a store policy whenever you like.", CLARIFY
            )
        if "account" in p.intents:
            return Response(
                "I can't see orders, accounts, payments, stores or offers. I can only answer from the product "
                "catalogue and the store's returns, exchange, shipping, cancellation and warranty policies.",
                REFUSE,
            )
        if "greet" in p.intents:
            return Response(
                "Hi! I can help you find products, check price, stock and ratings, and answer "
                "questions about returns, shipping and warranty. What do you need?",
                CLARIFY,
            )
        if "policy" in p.intents:
            if p.external and not (p.product_id or p.mentioned):  # "warranty on samsung tv"
                return refusals.external_policy(p, self._policy(p))
            return self._policy(p)
        resp = self._product_route(p, session)
        # "price of X and what is the return policy" answers both; "kurta ka size chota, exchange ho jayega?" asks
        # the policy, and "which kurta?" would not answer it
        policy_first = resp.action == CLARIFY and not p.intents & {"price", "stock", "rating"}
        if "policy_q" in p.intents and (resp.action != CLARIFY or policy_first):
            pol = self._policy(p)
            if pol.action == ANSWER:
                if resp.action != ANSWER:
                    return pol
                both = Response(f"{resp.text}\n{pol.text}", ANSWER, claims=resp.claims)
                both.claims.merge(pol.claims)
                return both
        return resp

    def _policy(self, p: Parsed) -> Response:
        """Quote the policy, or say it is silent when the question names a time it never mentions."""
        silent = [w for w in dict.fromkeys(p.keywords) if w in POLICY_TIME_WORDS and w not in self.policy_vocab]
        if silent:  # "do you deliver on sunday": the policy never mentions Sundays
            return policy_silent(self.policy, p, silent, self.policy_max_sentences)
        return policy_answer(self.policy, self.semantic, p, self.policy_max_sentences, self.policy_words)

    def _product_route(self, p: Parsed, session: Session) -> Response:
        """Answer a product message: one product, a clarifying question, a listing, or a refusal."""
        early = self._compare_unsold_or_choice(p, session)
        if early is not None:
            return early
        forced, spec = self._resolve(p, session)
        if forced is None and p.detail_keys and session.shown and not _names_products(p):
            shown = [self.index.products[pid] for pid in session.shown]  # "which sizes?" after a listing
            return product_answers.shown_details(shown, p.detail_keys)
        if forced is not None:
            session.last_product = forced.id
            return product_answers.product_answer(self.index, forced, p)
        if forced is None and p.intents & {"price", "stock"} and session.shown and spec.is_empty():
            shown = [self.index.products[pid] for pid in session.shown]  # "how much is it?" after a listing
            return self._one_of(p, shown, session)
        if not (self._names_a_kind(p) or any(k in spec.filters for k in KIND_FILTERS) or p.mentioned or p.product_id):
            need = self._for_a_need(p, spec, session)  # "ghar pe workout ke liye kya chahiye", "mummy ka birthday gift"
            if need is not None:
                return need
        if self._asks_what_the_catalogue_lacks(p):
            return Response("The catalogue doesn't record that, so I can't say.", REFUSE)
        _treat_description_words_as_unknown(p, spec, session)
        if self._names_nothing_sold(p) and not (p.refined and p.has_constraint):  # "budget 800 lopu undali"
            return self._unplaced_words(p)
        if _asks_for_nothing(spec):  # "not nike" alone says what isn't wanted
            return Response(ASK_WHAT, CLARIFY)
        return self._search(p, spec, session)

    # -- chat memory (web chat only: Session.carry) --------------------------------------------
    def _kind_tokens(self, spec: Spec) -> list[str]:
        """The product words of a search ("jean", "hoodie"), from its words or its category filters."""
        tokens = list(spec.type_tokens)
        for key in KIND_FILTERS:
            tokens += tokenize(spec.filters.get(key, ""))
        return list(dict.fromkeys(tokens))

    def _remember_search(self, session: Session) -> None:
        """Put the search being left into the chat's history (replacing an older one for the same product)."""
        spec = session.spec
        kinds = self._kind_tokens(spec)
        if not kinds:
            return
        label = " ".join(self.index.display_word(t) for t in spec.type_tokens) or spec.filters.get(KIND_KEY, "")
        label = label or spec.filters.get("category", "") or " ".join(kinds)
        session.history = [e for e in session.history if set(e["kinds"]) != set(kinds)]
        session.history.append({"label": label, "kinds": kinds, "spec": spec_to_dict(spec)})
        del session.history[:-MAX_HISTORY]

    def _go_back(self, p: Parsed, session: Session) -> Spec | None:
        """ "Back to the jeans", "jeans again": the earlier search for that product, with anything the message adds."""
        if not (BACK_WORDS & p.all_tokens) or not session.history:
            return None
        named = p.all_tokens | set(p.spec.type_tokens)
        entry = next((e for e in reversed(session.history) if set(e["kinds"]) & named), None)
        if entry is None:
            return None
        restored = spec_from_dict(entry["spec"])
        session.history.remove(entry)  # it is the current search again
        if set(self._kind_tokens(session.spec)) != set(self._kind_tokens(restored)):
            self._remember_search(session)  # the search being left, unless it is the same product
        p.unknown = [w for w in p.unknown if w not in BACK_WORDS]  # "back" is not a product word we failed to place
        extra = p.spec.copy()
        extra.type_tokens = []  # the product is already in the restored search
        p.refined = True
        return _refine(restored, extra)

    def _with_carry(self, spec: Spec, prev: Spec, p: Parsed) -> tuple[Spec, str | None]:
        """`spec` (a new product) with the earlier search's budget, size, brand/colour/gender and exclusions added when
        the message doesn't say its own. If nothing matches with all of them, drop them in a fixed order (size, then
        filters and exclusions, then the budget) until something matches. Returns the spec and a line saying what was
        kept or dropped (None when there was nothing to carry)."""
        budget = (prev.price_min, prev.price_max) if spec.price_min is None and spec.price_max is None else (None, None)
        layers: dict[str, object] = {}
        if budget != (None, None):
            layers["budget"] = budget
        if prev.size and spec.size is None:
            layers["size"] = prev.size
        filters = {k: v for k, v in prev.filters.items() if k in IDENTIFYING and k not in spec.filters}
        if filters:
            layers["filters"] = filters
        exclude = {k: vs for k, vs in prev.exclude.items() if k not in spec.exclude}
        if exclude:
            layers["exclude"] = exclude
        if not layers:
            return spec, None
        for dropped in ((), ("size",), ("size", "filters", "exclude"), ("size", "filters", "exclude", "budget")):
            kept = [name for name in layers if name not in dropped]
            candidate = self._carried(spec, prev, layers, kept)
            if kept and self.index.search(candidate, p.all_tokens)[0]:
                return candidate, self._carry_note(spec, layers, kept)
        return spec, self._carry_note(spec, layers, [], none_fit=True)

    @staticmethod
    def _carried(spec: Spec, prev: Spec, layers: dict, kept: list[str]) -> Spec:
        """A copy of `spec` with the named layers of the earlier search added."""
        out = spec.copy()
        if "budget" in kept:
            out.price_min, out.price_max = layers["budget"]  # type: ignore[misc]
        if "size" in kept:
            out.size, out.size_soft = prev.size, prev.size_soft
        if "filters" in kept:
            out.filters.update(layers["filters"])  # type: ignore[arg-type]
            for key in layers["filters"]:  # type: ignore[attr-defined]
                out.alternatives[key] = list(prev.alternatives.get(key, []))
        if "exclude" in kept:
            for key, values in layers["exclude"].items():  # type: ignore[attr-defined]
                out.exclude[key] = list(values)
        return out

    def _carry_note(self, spec: Spec, layers: dict, kept: list[str], none_fit: bool = False) -> str:
        """ "(Kept from your earlier search: black, men, under ₹1,500.)", plus what had to be dropped."""

        def words(names: list[str]) -> list[str]:
            """Plain words for some layers ("under ₹1,500", "size 9", "black")."""
            out: list[str] = []
            for name in names:
                value = layers[name]
                if name == "budget":
                    low, high = value  # type: ignore[misc]
                    # in rupees without the sign: this line repeats the shopper's own budget and cites no product,
                    # and the grounding check treats a line with a rupee sign as a price that needs a citation
                    out += ([f"over {_rupees(low)}"] if low is not None else []) + (
                        [f"under {_rupees(high)}"] if high is not None else []
                    )
                elif name == "size":
                    out.append(f"size {value}")
                elif name == "filters":
                    out += [str(v) for v in value.values()]  # type: ignore[attr-defined]
                else:
                    out += [f"not {v}" for vs in value.values() for v in vs]  # type: ignore[attr-defined]
            return out

        if none_fit:
            return f"(Nothing from your earlier search fits ({', '.join(words(list(layers)))}), so I started fresh.)"
        line = f"(Kept from your earlier search: {', '.join(words(kept))}."
        dropped = [name for name in layers if name not in kept]
        if dropped:
            line += f" Dropped {', '.join(words(dropped))}: nothing matches with them.)"
        else:
            line += ")"
        return line

    def _budget_ruled_out(self, spec: Spec, p: Parsed) -> bool:
        """Nothing fits the budget, but the same search without it finds products: the budget is what failed."""
        if spec.price_min is None and spec.price_max is None:
            return False
        looser = spec.copy()
        looser.price_min = looser.price_max = None
        return bool(self.index.search(looser, p.all_tokens)[0])

    def _for_a_need(self, p: Parsed, spec: Spec, session: Session) -> Response | None:
        """A need or occasion and no product named: one well-rated product of each kind that serves it, within any
        budget ("ghar pe workout": a yoga mat, dumbbells, resistance bands, ...). A gift with nothing else to go on
        asks who it is for. None when the message names neither."""
        # "treadmill ghar ke liye" asks for one thing by name; a need is asked as "kuch", "kya kya", "something"
        general = bool(p.all_tokens & GENERAL_ASK_WORDS)
        kinds = next((kinds for words, kinds in NEEDS if p.all_tokens & words), None) if general else None
        if kinds and not all(
            self._fits_some(value, kinds) for key, value in p.spec.filters.items() if key not in IDENTIFYING
        ):
            return None  # "a wooden study table": a table is asked for, which none of the desk kinds is
        picks = [hit for hit in (self._best_of_kind(p, spec, kind) for kind in kinds or ()) if hit][
            : self.list_limit + 1
        ]
        if picks:
            claims = Claims()
            lines = ["Here are a few things that could help:"] + [product_line(h, claims) for h in picks]
            session.last_product, session.shown = None, [h.id for h in picks]
            return Response("\n".join(lines), ANSWER, claims=claims)
        if p.all_tokens & GIFT_WORDS and not p.all_tokens & GIFT_SERVICES:  # not "gift wrap karke bhej sakte ho?"
            return Response(GIFT_QUESTION, CLARIFY)
        return None

    def _fits_some(self, value: str, kinds: list[str]) -> bool:
        """Whether some product of these kinds can have this value ("dry" for a body lotion's skin type)."""
        toks = tuple(tokenize(value))
        return any(toks in self.index.attr_values.get(k, {}) for kind in kinds for k in self.index.kind_keys(kind))

    def _best_of_kind(self, p: Parsed, spec: Spec, kind: str) -> Product | None:
        """The top-ranked product of one kind within the budget, keeping filters that kind has ("dry" skin)."""
        keys = self.index.kind_keys(kind)
        if not keys:
            return None  # a kind this catalogue doesn't sell
        wanted = Spec(filters={KIND_KEY: kind}, price_min=spec.price_min, price_max=spec.price_max)
        for key, value in p.spec.filters.items():
            toks = tuple(tokenize(value))
            other = next((k for k in sorted(keys) if toks in self.index.attr_values.get(k, {})), None)
            if key in keys or other:
                wanted.filters[key if key in keys else other] = value
        hits, _ = self.index.search(wanted, p.all_tokens)
        return hits[0] if hits else None

    def _one_of(self, p: Parsed, hits: list[Product], session: Session) -> Response:
        """The shopper means one of these products (rule 6.2): answer when they all give the same answer to what
        was asked, refuse when none of them lists it, and ask which one only when the answers differ."""
        window = hits[:SCAN_LIMIT]  # a whole-set check on a huge result adds nothing a few thousand don't show
        keys = [k for k in p.detail_keys if any(h.attr_values(k) for h in window)]
        if p.detail_keys and not keys:  # "iron ka weight kitna hai": no iron lists a weight
            names = " or ".join(dict.fromkeys(field_label(k) for k in p.detail_keys))
            return Response(f"The catalogue does not list {names} for these products.", REFUSE)
        asked = [lambda h, k=k: tuple(h.attr_values(k)) for k in keys]
        if "price" in p.intents:
            asked.append(lambda h: h.price)
        if "stock" in p.intents:
            asked.append(lambda h: (h.in_stock, h.stock_status))  # the flag every catalogue fills, and the pack's value
        if "rating" in p.intents:
            asked.append(lambda h: h.rating)
        if asked and all(_all_alike(window, get) for get in asked):
            session.last_product, session.shown = None, [h.id for h in hits[: self.list_limit]]
            if keys:
                return product_answers.shown_details(hits[: self.list_limit], keys)
            return listings.listing(hits, p, p.spec, False, self.list_limit)
        session.last_product, session.shown = None, [h.id for h in hits[: self.clarify_limit]]
        return listings.clarify(self.index, window, self.clarify_limit, len(hits))

    def _names_a_kind(self, p: Parsed) -> bool:
        """The message names a kind of product sold, by its category or by the noun that names it."""
        return any(k in p.spec.filters for k in KIND_FILTERS) or any(t in self.head_nouns for t in p.spec.type_tokens)

    def _asks_what_the_catalogue_lacks(self, p: Parsed) -> bool:
        """A question about a kind of product that asks nothing the catalogue records ("is the lip balm good for
        daily use?", "sunscreen ka expiry date kya hai?"): refused (rule 6.3), not answered with a list."""
        if not p.is_question or p.intents & (FACTS | {"policy", "policy_q"}) or p.product_id or p.mentioned:
            return False
        if p.all_tokens & BROWSE_W or not self._names_a_kind(p):
            return False
        names_one = "brand" in p.spec.filters or any(
            self.index.product_count(t) < COMMON_WORD_PRODUCTS for t in p.spec.type_tokens
        )
        if names_one:  # a named product ("Zentrix LarkFit earbuds ...") is answered about that product instead
            return False
        if any(k not in KIND_FILTERS + IDENTIFYING for k in p.spec.filters):  # "suit dry skin?": skin_type is
            return False  # recorded; a brand or colour only says which product ("beige kurta made in india?")
        return bool(set(p.unknown) - set(p.external) or p.spec.desc_tokens)

    def _compare_unsold_or_choice(self, p: Parsed, session: Session) -> Response | None:
        """Messages answered before any search: two named products, a brand the shop lacks, "A or B"."""
        if len(p.mentioned) >= 2 and not p.product_id:
            session.shown = p.mentioned[:4]
            return product_answers.compare(self.index, p.mentioned, p)
        if p.external and not (p.product_id or p.mentioned):
            return refusals.external(self.index, p, self.list_limit)
        unsold = sorted(p.all_tokens & UNSOLD_PRODUCTS)
        if unsold and not self._names_a_kind(p):  # "can I get a laptop here?": not the laptop sleeves
            return Response(f"I don't have {' or '.join(unsold)} in the catalogue.", REFUSE)
        if p.choice:
            return listings.choice(self.index, p, self.list_limit)
        return None

    def _names_nothing_sold(self, p: Parsed) -> bool:
        """Unplaced words outweigh the catalogue words, and none of those names a kind of product we sell.

        "sneakers for gym": shoes are sold, gym is a modifier. But a describing word alone ("wireless mouse",
        "electric scooter") doesn't name a product we sell, and neither does a colour. A fallback that already
        identified the product type counts as naming it.
        """
        known = len(p.spec.type_tokens) + len(p.spec.desc_tokens) + len(p.spec.filters)
        names_product = self._names_a_kind(p) or bool(p.mapped)  # "powerbank ... rn": named as a category
        return bool(p.unknown) and not names_product and len(p.unknown) >= max(known, 1)

    def _unplaced_words(self, p: Parsed) -> Response:
        """Words the catalogue doesn't have: a policy question in other words, or a refusal."""
        if self.semantic is not None and not p.external:  # "money back timeline": a policy question in other words
            hit = self.semantic.policy_sentence(p.raw)
            if hit:
                sec, sentence, _ = hit
                return Response(f"{sentence} [policy:{sec.id}]", ANSWER, path="semantic", claims=cited_section(sec.id))
        if set(p.keywords) & self.policy_words:  # "GST alag se lagega": a policy question in product words
            pol = self._policy(p)
            if pol.action == ANSWER:
                return pol
        return refusals.unknown_refusal(self.index, p, self.list_limit)

    def _search(self, p: Parsed, spec: Spec, session: Session) -> Response:
        """Search with hard constraints. With chat memory on (web only), a new product also gets the earlier search's
        budget, size and brand/colour/gender, and the reply says what was kept or dropped."""
        note = None
        if session.carry_from is not None:
            spec, note = self._with_carry(spec, session.carry_from, p)
            session.carry_from = None
        resp = self._search_core(p, spec, session)
        if note and resp.action == ANSWER:
            resp.text = f"{note}\n{resp.text}"
        return resp

    def _search_core(self, p: Parsed, spec: Spec, session: Session) -> Response:
        """Search with hard constraints, then name one product, ask which one, or list the best matches."""
        unlisted = _drop_unlisted_filters(self.index, spec, p)
        hits, relaxed = self.index.search(spec, p.all_tokens)
        if not hits:
            if self._budget_ruled_out(spec, p):  # "under 500" found nothing: the next turn is still under 500
                session.spec, session.budget_kept = spec, True
            return refusals.no_match(self.index, spec, p, self.list_limit)
        if _off_topic_refinement(self.index, p, hits, relaxed):
            # "laptop" after "black hoodie": none of the products being discussed match, so don't re-show them
            return Response(
                f'I don\'t have anything matching "{" ".join(p.spec.desc_tokens)}" among the products we were '
                'looking at. Tell me what you\'d like instead, or say "start over".',
                REFUSE,
            )
        session.spec, session.budget_kept = spec, False
        pick = self._pick_one(p, spec, hits)
        if pick is _AMBIGUOUS:
            return self._one_of(p, hits, session)
        if pick is not None:
            session.last_product = pick.id
            return product_answers.product_answer(self.index, pick, p, lead=SORT_LABELS.get(spec.sort))
        session.last_product = hits[0].id if len(hits) == 1 else None
        session.shown = [h.id for h in hits[: self.list_limit]]
        resp = listings.listing(hits, p, spec, relaxed, self.list_limit)
        if unlisted:
            resp.text += (
                f"\n(The catalogue doesn't list {' or '.join(unlisted)} for these products, so I couldn't "
                "filter by it.)"
            )
        return resp

    def _pick_one(self, p: Parsed, spec: Spec, hits: list[Product]) -> Product | object | None:
        """The one product the message is about; _AMBIGUOUS when several fit and differ; None for a listing."""
        specific = p.intents & {"price", "rating", "detail"}
        if p.refined and (p.spec.size or p.spec.filters) and not specific:
            return None  # "size 9 available aa?" after a listing: a narrower listing, not one product
        # "is the grey backpack in stock", "Sanchayo NimbusMax container set stock me hai?": about these products
        about_these = {"the", "this", "that"} & p.all_tokens or (p.is_question and len(hits) <= self.clarify_limit)
        if "stock" in p.intents and about_these:
            specific = specific | {"stock"}
        if len(hits) == 1:
            return hits[0] if specific else None
        if spec.sort and not p.spec.type_tokens and not specific and {"the", "one"} & p.all_tokens:
            return hits[0]  # "the cheapest" after a search
        if p.superlative:
            return hits[0]  # "sabse sasti king bedsheet": one product, not a list
        if not specific:
            return None
        if spec.sort:
            return hits[0]
        if spec.price_max is not None or spec.price_min is not None:  # a budget implies browsing, not one product
            return None
        exact = self.index.exact_title_matches(hits, p.all_tokens)
        return exact[0] if len(exact) == 1 else _AMBIGUOUS

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
        if session.carry:
            back = self._go_back(p, session)
            if back is not None:
                return None, back
        # a different kind of product ("cotton kurti" after "blue running shoes") is a new search, whether it was
        # named by product words or read as a category, so the earlier colour or type doesn't carry over (unless the
        # chat has memory switched on: then the budget, size and brand/colour/gender are carried over, see _with_carry)
        new_kind = any(k in s.filters and s.filters[k] != session.spec.filters.get(k) for k in KIND_FILTERS)
        if s.type_tokens or new_kind or session.spec.is_empty() or not (p.has_constraint or s.sort or s.desc_tokens):
            if session.carry and (s.type_tokens or new_kind) and not session.spec.is_empty():
                if set(self._kind_tokens(session.spec)) != set(self._kind_tokens(s)):
                    self._remember_search(session)  # moving to another product, not narrowing this one
                session.carry_from = session.spec.copy()
            return None, s.copy()
        if session.budget_kept and set(p.unknown) - set(p.external):
            # the kept search failed on its budget; a message with a word we can't place ("hudieee under 1500") is a new
            # request, not a follow-up, so the failed search's product must not be shown for it
            return None, s.copy()
        merged = _refine(session.spec, s)  # a refinement of the previous search
        p.refined = True
        return None, merged


def _rupees(amount: float) -> str:
    """ "3,000 rupees": an amount the shopper gave, in words rather than with the rupee sign."""
    return f"{format_inr(amount).lstrip('₹')} rupees"


def _refine(base: Spec, s: Spec) -> Spec:
    """`base` with what a follow-up message says laid over it: new filters replace old ones, exclusions and description
    words add up, and a new budget, size or sort replaces the earlier one."""
    merged = base.copy()
    merged.filters.update(s.filters)
    for key in s.filters:  # a new value for a filter replaces the old one and its "or" alternatives
        merged.alternatives[key] = list(s.alternatives.get(key, []))
    for key, values in s.exclude.items():
        merged.exclude[key] = list(dict.fromkeys(merged.exclude.get(key, []) + values))
    merged.desc_tokens = list(dict.fromkeys(merged.desc_tokens + s.desc_tokens))
    for attr in ("price_min", "price_max", "size", "sort"):
        if getattr(s, attr) is not None:
            setattr(merged, attr, getattr(s, attr))
    if s.size is not None:
        merged.size_soft = s.size_soft
    return merged


def _drop_unlisted_filters(index: CatalogueIndex, spec: Spec, p: Parsed) -> list[str]:
    """Drop filters on an attribute that none of the requested products has (\"smartwatch for women\" when
    smartwatches list no gender): filtering by it would wrongly refuse. Returns the dropped attribute names."""
    if not (spec.filters and spec.type_tokens):
        return []
    base = spec.copy()
    base.filters, base.price_min, base.price_max, base.size = {}, None, None, None
    pool, _ = index.match_ids(base)
    products = index.products
    dropped = [k for k in spec.filters if pool and not any(products[i].attr_values(k) for i in pool)]
    for k in dropped:
        del spec.filters[k]
    return dropped


def _policy_terms(policy: list[Section], index: CatalogueIndex) -> frozenset:
    """Named terms of the policy: words it writes in capitals (GST, COD, MRP, UPI) and no product uses.

    One of these alone identifies a policy question ("GST alag se lagega?"). Ordinary words, even rare ones like
    "fee", don't: a question about a fee the policy never mentions must still be refused.
    """
    terms = {w for sec in policy for w in re.findall(r"\b[A-Z]{2,}\b", f"{sec.heading} {sec.body}")}
    return frozenset(t for w in terms for t in tokenize(w) if t not in index.vocab)


def _all_alike(items: list[Product], get: Callable[[Product], object]) -> bool:
    """Whether `get` gives the same value for every product (stops at the first that differs)."""
    first = get(items[0])
    return all(get(h) == first for h in items)


def _names_products(p: Parsed) -> bool:
    """The message names products or a new search of its own, so it isn't a follow-up about the last listing."""
    s = p.spec
    return bool(p.product_id or p.mentioned or s.type_tokens or s.filters or s.desc_tokens)


GIFT_QUESTION = (
    "Happy to help with a gift. Who is it for, and what do they like? Tell me a kind of product and a budget, and "
    "I'll show you options."
)
ASK_WHAT = "What are you looking for? Tell me the kind of product, and a brand, colour or budget if you have one."
SCAN_LIMIT = 2000  # most results looked through when asking "do these all give the same answer?"
_AMBIGUOUS = object()  # _pick_one's answer when several products fit "the" product and differ
BACK_WORDS = frozenset({"back", "again", "earlier", "previous", "before", "pehle", "wapas", "phir", "pichle"})
MAX_HISTORY = 8  # earlier searches kept for "back to the jeans" (web chat only)
KIND_FILTERS = ("category", "sub_category")  # filters that name the kind of product itself
IDENTIFYING = ("brand", "color", "colour", "gender")  # filters that say which product, not what is asked about it


def _treat_description_words_as_unknown(p: Parsed, spec: Spec, session: Session) -> None:
    """Only description words ("laptop" in a backpack's text) don't name a product the shop sells."""
    if not spec.type_tokens and not spec.filters and spec.desc_tokens and session.spec.is_empty():
        p.unknown = p.unknown + spec.desc_tokens
        p.spec.desc_tokens = spec.desc_tokens = []


def _asks_for_nothing(spec: Spec) -> bool:
    """No product, filter, budget or size: the message says what isn't wanted, or nothing at all."""
    only_exclusions = not (
        spec.type_tokens
        or spec.filters
        or spec.desc_tokens
        or spec.size
        or spec.sort
        or spec.price_min is not None
        or spec.price_max is not None
    )
    return (spec.is_empty() or only_exclusions) and not spec.sort


def _off_topic_refinement(index: CatalogueIndex, p: Parsed, hits: list[Product], relaxed: bool) -> bool:
    """A follow-up adds only description words that none of the products being discussed has."""
    s = p.spec
    only_words = not (s.filters or s.size or s.sort or s.price_min is not None or s.price_max is not None)
    new_words = s.desc_tokens
    return bool(
        p.refined
        and relaxed
        and new_words
        and only_words
        and not any(t in index.desc_tokens[h.id] for h in hits for t in new_words)
    )
