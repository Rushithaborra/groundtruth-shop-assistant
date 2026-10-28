"""Query understanding: rule-based, so it runs offline and every decision is inspectable.

Handles short, ungrammatical, code-mixed (English/Hinglish) shopper queries.
"""

from __future__ import annotations

import re

from ..catalogue import CatalogueIndex
from ..text import ABROAD, ATTRIBUTE_WORDS, EXTERNAL_BRANDS, RELATED, STOPWORDS, tokenize
from .lexicon import (
    ACCOUNT_PHRASES,
    ACCOUNT_W,
    BACKPACK_PHRASE,
    BEST_W,
    CHEAP_W,
    EXPENSIVE_W,
    EXPLICIT_PRICE_W,
    GREET_W,
    HELP_PHRASES,
    HELP_W,
    IGNORE,
    NEG_W,
    POLICY_W,
    PRICE_W,
    QUESTION_W,
    RATING_W,
    RESET_PHRASES,
    STOCK_W,
    SUPERLATIVE_W,
    THANKS_W,
    WH_W,
)
from .measures import extract_measures
from .money import extract_money
from .parsed import Parsed
from .sizes import extract_size
from .tokens import repair_tokens


def understand(query: str, index: CatalogueIndex) -> Parsed:
    """Read one shopper message into intents and a search spec, using only the catalogue's own vocabulary."""
    parsed = Parsed(raw=query)
    low, account, helpq = _normalise(query, index)
    if any(phrase in low for phrase in RESET_PHRASES):
        parsed.intents.add("reset")
        return parsed

    ids, low = _extract_ids(low, index)
    if len(ids) == 1:
        parsed.product_id = ids[0]
    parsed.spec.size, low, parsed.spec.size_soft = extract_size(low, index)
    low, parsed.spec.measures = extract_measures(low)
    low, parsed.spec.price_min, parsed.spec.price_max = extract_money(low)

    tokens = repair_tokens(tokenize(low), index)
    parsed.all_tokens = set(tokens)
    token_set = set(tokens)
    parsed.mentioned = (
        ids + [m for m in index.mentioned(tokens) if m not in ids] if len(ids) > 1 else index.mentioned(tokens)
    )

    asks = bool(token_set & (WH_W | QUESTION_W)) or "?" in query or bool(parsed.product_id)
    consumed = _match_attribute_names(parsed, tokens, index, asks)
    _apply_attribute_filters(parsed, tokens, index, consumed)
    _detect_fact_intents(parsed, token_set, index, asks)
    _detect_sort(parsed, token_set)
    _classify_tokens(parsed, tokens, consumed, index)
    if token_set & ABROAD:  # "delivery to dubai" is the "outside India" shipping question
        parsed.keywords += ["outside", "india"]
    _detect_conversation_intents(parsed, token_set, account, helpq)
    return parsed


def _normalise(query: str, index: CatalogueIndex) -> tuple[str, bool, bool]:
    """Lower-case and rewrite phrases; returns (text, asks about an account, asks for help)."""
    low = " ".join(query.lower().replace("t-shirt", "tshirt").split())
    low = re.sub(r"\bnoise[\s-]*(cancel\w*|isolat\w*|reduc\w*)", "anc", low)  # a feature, not the brand Noise
    account = any(ph in low for ph in ACCOUNT_PHRASES)
    helpq = any(ph in low for ph in HELP_PHRASES)
    if "backpack" in index.type_vocab:
        low = BACKPACK_PHRASE.sub(lambda m: f"{m.group(1)} backpack", low)
    return low, account, helpq


def _extract_ids(low: str, index: CatalogueIndex) -> tuple[list[str], str]:
    """Explicit product ids ("P0042"); two or more ("compare P0042 and P0057") are a comparison."""
    ids = []
    for word in re.findall(r"[a-z0-9_-]+", low):
        pid = index.ids_upper.get(word.upper())
        if pid and pid not in ids:
            ids.append(pid)
            low = re.sub(rf"\b{re.escape(word)}\b", " ", low, count=1)
    return ids, low


def _match_attribute_names(parsed: Parsed, tokens: list[str], index: CatalogueIndex, asks: bool) -> list[bool]:
    """Multi-word attribute names ("shoe compartment" -> shoe_compartment) are questions about that attribute, not
    product words: consumed here so "shoe" isn't read as the product type. Returns which tokens were consumed."""
    consumed = [False] * len(tokens)
    for toks, key in index.attr_key_phrases.items():
        n = len(toks)
        for i in range(len(tokens) - n + 1):
            if tuple(tokens[i : i + n]) == toks and not any(consumed[i : i + n]):
                consumed[i : i + n] = [True] * n
                if asks and key not in parsed.detail_keys:
                    parsed.detail_keys.append(key)
    return consumed


def _apply_attribute_filters(parsed: Parsed, tokens: list[str], index: CatalogueIndex, consumed: list[bool]) -> None:
    """Attribute-value filters (colour, material, brand, ...), longest values first; "not X" becomes an exclusion.

    Marks the tokens used as filter values in `consumed`.
    """
    for n, toks, key, val in index.attr_candidates:
        for i in range(len(tokens) - n + 1):
            if tuple(tokens[i : i + n]) == toks and not any(consumed[i : i + n]):
                if i > 0 and tokens[i - 1] in NEG_W:
                    parsed.spec.exclude.setdefault(key, []).append(val)
                elif key not in parsed.spec.filters:
                    parsed.spec.filters[key] = val
                for j in range(i, i + n):
                    consumed[j] = True
                break


def _detect_fact_intents(parsed: Parsed, token_set: set[str], index: CatalogueIndex, asks: bool) -> None:
    """Price, stock, rating, and attribute questions ("what colour", "kitni moti", "material of P0042")."""
    if token_set & PRICE_W:
        parsed.intents.add("price")
    if token_set & STOCK_W:
        parsed.intents.add("stock")
    if token_set & RATING_W:
        parsed.intents.add("rating")
    asked_words = token_set | {ATTRIBUTE_WORDS[t] for t in token_set if t in ATTRIBUTE_WORDS}  # "moti" -> thickness
    for ktok, keys in index.attr_key_groups.items():
        if ktok not in asked_words or ktok in PRICE_W or not asks:
            continue
        for key in keys:  # "water" asks about water_resistant and water_resistance_rating
            if key not in parsed.spec.filters and key not in parsed.spec.exclude and key not in parsed.detail_keys:
                parsed.detail_keys.append(key)
    if "size" in token_set and not parsed.spec.size and asks and "size" not in parsed.detail_keys:
        parsed.detail_keys.append("size")
    if parsed.detail_keys:
        parsed.intents.add("detail")
        if not token_set & EXPLICIT_PRICE_W:  # "kitni moti" asks how thick, not how much
            parsed.intents.discard("price")


def _detect_sort(parsed: Parsed, token_set: set[str]) -> None:
    """Cheapest, costliest or best-rated first."""
    if token_set & CHEAP_W:
        parsed.spec.sort = "price_asc"
    elif token_set & EXPENSIVE_W:
        parsed.spec.sort = "price_desc"
    elif token_set & BEST_W:
        parsed.spec.sort = "price_desc" if ("price" in token_set and "highest" in token_set) else "rating_desc"
        if "price" in token_set and "highest" in token_set:
            parsed.intents.discard("rating")

    if "least" in token_set and token_set & EXPENSIVE_W:
        parsed.spec.sort = "price_asc"
    parsed.superlative = bool(parsed.spec.sort and token_set & SUPERLATIVE_W)
    if parsed.superlative and token_set & STOCK_W:  # "sabse sasti jo stock me ho": only products that can be ordered
        parsed.spec.in_stock_only = True


def _classify_tokens(parsed: Parsed, tokens: list[str], consumed: list[bool], index: CatalogueIndex) -> None:
    """Remaining content tokens become product-type words, description words, or unknown words."""
    for i, tok in enumerate(tokens):
        if tok not in STOPWORDS and not tok.isdigit():
            parsed.keywords.append(tok)
        if consumed[i] or tok in IGNORE or tok in index.attr_keys or tok.isdigit() or len(tok) < 2:
            continue
        if tok not in index.type_vocab and tok not in index.desc_vocab and len(tok) >= 5:
            tok = index.closest(tok) or tok  # typos: "runing"
        if tok in index.type_vocab:
            if tok not in parsed.spec.type_tokens:
                parsed.spec.type_tokens.append(tok)
        elif tok in index.desc_vocab:
            if tok not in parsed.spec.desc_tokens:
                parsed.spec.desc_tokens.append(tok)
        else:
            if tok in EXTERNAL_BRANDS and i and tokens[i - 1] in NEG_W:
                # "not nike": a brand the shop doesn't carry is excluded by definition; keep it as an exclusion so a
                # follow-up turn still refines the earlier search instead of looking empty
                parsed.spec.exclude.setdefault("brand", []).append(tok)
                continue
            parsed.unknown.append(tok)
            if tok in EXTERNAL_BRANDS:
                parsed.external.append(tok)
            elif tok in RELATED and RELATED[tok] in index.type_vocab:
                parsed.alternatives.append(RELATED[tok])


def _detect_conversation_intents(parsed: Parsed, token_set: set[str], account: bool, helpq: bool) -> None:
    """Policy questions, greetings, help, thanks, out-of-scope account questions; plain search otherwise."""
    explicit_price = bool(token_set & {"price", "much"})
    product_ref = parsed.has_product_ref
    asks_fact = (
        explicit_price or "rating" in parsed.intents or (bool(parsed.intents & {"stock", "detail"}) and product_ref)
    )
    if token_set & POLICY_W:
        parsed.intents.add("policy_q" if asks_fact and product_ref else "policy")
    if token_set & GREET_W and not (parsed.has_constraint or parsed.intents):
        parsed.intents.add("greet")
    if (helpq or token_set & HELP_W) and not (parsed.has_constraint or parsed.intents - {"search"}):
        parsed.intents.add("greet")
    content = token_set - STOPWORDS - THANKS_W - GREET_W
    if token_set & THANKS_W and not content and not parsed.has_constraint:
        parsed.intents.add("thanks")
    if (account or (token_set & ACCOUNT_W and not parsed.has_product_ref)) and not (token_set & POLICY_W):
        parsed.intents.add("account")
    if not parsed.intents:
        parsed.intents.add("search")
