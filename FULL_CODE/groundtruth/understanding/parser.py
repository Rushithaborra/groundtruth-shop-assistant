"""Query understanding: rule-based, so it runs offline and every decision is inspectable.

Handles short, ungrammatical, code-mixed (English/Hinglish) shopper queries.
"""

from __future__ import annotations

import re

from ..catalogue import KIND_KEY, CatalogueIndex
from ..text import ABROAD, ATTRIBUTE_WORDS, EXTERNAL_BRANDS, RELATED, STOPWORDS, normalise_word, tokenize
from .lexicon import (
    ACCOUNT_PHRASES,
    ACCOUNT_W,
    BACKPACK_PHRASE,
    BEST_W,
    CHEAP_W,
    CHOICE_W,
    EXPENSIVE_W,
    EXPLICIT_PRICE_W,
    GREET_W,
    HELP_PHRASES,
    HELP_W,
    HOW_TO,
    IGNORE,
    NEG_W,
    OR_W,
    POLICY_W,
    PREFERENCE_W,
    PRICE_W,
    QUESTION_W,
    RATING_W,
    RESET_PHRASES,
    STOCK_W,
    SUPERLATIVE_W,
    THANKS_INTENSIFIERS,
    THANKS_W,
    WH_W,
)
from .measures import extract_measures
from .money import extract_money
from .parsed import Parsed
from .sizes import extract_size
from .tokens import join_split_names, repair_tokens

# A word in at least this many products is a common word ("gym", "office"), not the name of a product line
COMMON_WORD_PRODUCTS = 20


def _names_sizes_and_budget(parsed: Parsed, low: str, index: CatalogueIndex) -> tuple[str, list[str]]:
    """Take product ids, named products, size, measurements and budget out of the message; return what is left and
    its tokens."""
    ids, low = _extract_ids(low, index)
    if len(ids) == 1:
        parsed.product_id = ids[0]
    # product names are matched on the words as typed, before sizes, measurements and budgets are taken out:
    # "Chargit RippleZip 20 W Bluetooth Speaker" names a product, it doesn't ask for 20 W
    named = index.mentioned(repair_tokens(tokenize(join_split_names(low, index)), index))
    parsed.spec.size, low, parsed.spec.size_soft = extract_size(low, index)
    low, parsed.spec.measures = extract_measures(low)
    low, parsed.spec.price_min, parsed.spec.price_max = extract_money(low)

    tokens = repair_tokens(tokenize(join_split_names(low, index)), index)
    parsed.all_tokens = set(tokens)
    parsed.mentioned = ids + [m for m in named if m not in ids] if len(ids) > 1 else named
    if parsed.mentioned:
        parsed.spec.measures = []  # they were part of the named products' names
    return low, tokens


def understand(query: str, index: CatalogueIndex) -> Parsed:
    """Read one shopper message into intents and a search spec, using only the catalogue's own vocabulary."""
    parsed = Parsed(raw=query)
    low, account, helpq = _normalise(query, index)
    if any(phrase in low for phrase in RESET_PHRASES):
        parsed.intents.add("reset")
        return parsed

    low, tokens = _names_sizes_and_budget(parsed, low, index)
    token_set = set(tokens)
    asks = bool(token_set & (WH_W | QUESTION_W)) or "?" in query or bool(parsed.product_id)
    parsed.is_question = asks
    consumed = _match_attribute_names(parsed, tokens, index, asks)
    attribute_names = list(consumed)  # words used as an attribute's name ("shoe compartment")
    _apply_attribute_filters(parsed, tokens, index, consumed)
    _fit_to_kind(parsed, tokens, consumed, attribute_names, index)
    _detect_fact_intents(parsed, tokens, index, asks)
    _detect_sort(parsed, token_set)
    _classify_tokens(parsed, tokens, consumed, index)
    _soften_other_kinds_words(parsed, index)
    if token_set & CHOICE_W and token_set & (PREFERENCE_W | WH_W | BEST_W) and not parsed.spec.sort:
        parsed.choice = _choice(tokens, index)  # "tshirt or shirt which is better"; "which is cheaper" is answerable
    if token_set & ABROAD:  # "delivery to dubai" is the "outside India" shipping question
        parsed.keywords += ["outside", "india"]
    _detect_conversation_intents(parsed, token_set, account, helpq)
    if parsed.intents & {"policy", "policy_q"} and HOW_TO.search(low):
        parsed.keywords.append("how")  # "how can I return": the "How to start a return" section
    return parsed


def _choice(tokens: list[str], index: CatalogueIndex) -> list[str]:
    """Two different kinds of product joined by "or" / "ya" / "vs" ("tshirt or shirt"), or nothing.

    Colours, materials and brands aren't kinds: "purple ya teal earbuds" is a choice between variants, which the
    catalogue can answer, not between kinds of product.
    """
    for i in range(1, len(tokens) - 1):
        a, b = tokens[i - 1], tokens[i + 1]
        if tokens[i] in CHOICE_W and a != b and _is_kind(a, index) and _is_kind(b, index):
            return [a, b]
    return []


def _is_kind(token: str, index: CatalogueIndex) -> bool:
    """A word naming a kind of product: in the catalogue's product words, and not a colour, material, brand, ..."""
    if token not in index.type_vocab:
        return False
    return not any((token,) in values for key, values in index.attr_values.items() if key != "sub_category")


def _normalise(query: str, index: CatalogueIndex) -> tuple[str, bool, bool]:
    """Lower-case and rewrite phrases; returns (text, asks about an account, asks for help)."""
    low = " ".join(query.lower().replace("t-shirt", "tshirt").split())
    low = re.sub(r"\bnoise[\s-]*(cancel\w*|isolat\w*|reduc\w*)", "anc", low)  # a feature, not the brand Noise
    low = re.sub(r"\bsame[\s-]*day\b", "sameday", low)  # one delivery-time term, like "express"
    account = any(ph in low for ph in ACCOUNT_PHRASES)
    helpq = any(ph in low for ph in HELP_PHRASES)
    if "backpack" in index.type_vocab:  # unless the catalogue itself sells "school bags"
        low = BACKPACK_PHRASE.sub(lambda m: _backpack_phrase(m, index), low)
    return low, account, helpq


def _backpack_phrase(m: re.Match[str], index: CatalogueIndex) -> str:
    """ "college bag" or "bags for college" is a backpack, unless the catalogue sells that kind by that name."""
    word = m.group(1) or m.group(2)
    return m.group(0) if word in index.type_vocab else f"{word} backpack"


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

    Marks the tokens used as filter values in `consumed`. A value of several words may have a typo in one of them
    when its other words are exact: "runniing shoes" is Running Shoes, not Sneakers ("shoes" alone).
    """
    present = set(tokens)
    typos = {t for t in tokens if t not in index.vocab and len(t) >= 4}  # words that could be a slip
    for n, toks, key, val in index.attr_candidates:
        missing = sum(t not in present for t in toks)
        if missing > (1 if typos and n >= 2 else 0):
            continue  # cheap rejection: most values can't match this message at all
        for i in range(len(tokens) - n + 1):
            window = tuple(tokens[i : i + n])
            same = window == toks or (missing and _one_slip(window, toks, typos, index))
            if same and not any(consumed[i : i + n]):
                if i > 0 and tokens[i - 1] in NEG_W:
                    parsed.spec.exclude.setdefault(key, []).append(val)
                elif key not in parsed.spec.filters:
                    parsed.spec.filters[key] = val
                elif OR_W & present and val != parsed.spec.filters[key]:  # "grey or black": either will do
                    parsed.spec.alternatives.setdefault(key, []).append(val)
                for j in range(i, i + n):
                    consumed[j] = True
                break
    _prefer_kind(parsed, index)


def _one_slip(window: tuple, value: tuple, typos: set[str], index: CatalogueIndex) -> bool:
    """The window spells the value except for one misspelt word that the typo matcher reads as the missing word."""
    off = [(w, v) for w, v in zip(window, value) if w != v]
    return len(off) == 1 and off[0][0] in typos and index.closest(off[0][0]) == off[0][1]


def _prefer_kind(parsed: Parsed, index: CatalogueIndex) -> None:
    """A word that is both an attribute value and a kind of product ("irons": material iron, or the Irons) names
    the product when no other kind is named. "iron tawa" keeps material iron with the Tawa kind."""
    if KIND_KEY in parsed.spec.filters:
        return
    kinds = index.attr_values.get(KIND_KEY, {})
    for key, val in list(parsed.spec.filters.items()):
        kind = kinds.get(tuple(tokenize(val)))
        if kind:
            del parsed.spec.filters[key]
            parsed.spec.filters[KIND_KEY] = kind
            return


def _fit_to_kind(
    parsed: Parsed, tokens: list[str], consumed: list[bool], attribute_names: list[bool], index: CatalogueIndex
) -> None:
    """Read the message against the kind of product it names.

    The most specific kind fully named wins ("shoes for daily walking" is Walking Shoes, not the Sneakers that "shoes"
    alone reads as), and a value goes to the attribute that kind has ("oily" is skin_type for sunscreen, not the
    hair_type of shampoos); a value no attribute of that kind holds becomes a soft description word.
    """
    words = {t for t, name in zip(tokens, attribute_names) if not name}  # not "shoe" in "shoe compartment"
    kinds = index.attr_values.get(KIND_KEY, {})
    named = [(len(toks), kind) for toks, kind in kinds.items() if set(toks) <= words]
    current = parsed.spec.filters.get(KIND_KEY)
    if named:
        size, best = max(named, key=lambda c: (c[0], c[1]))
        if current is None or size > len(tokenize(current)):
            parsed.spec.filters[KIND_KEY] = best
            for i, t in enumerate(tokens):
                if t in tokenize(best):
                    consumed[i] = True
    keys = index.kind_keys(parsed.spec.filters.get(KIND_KEY))
    if not keys:
        return
    for key, val in list(parsed.spec.filters.items()):
        if key in keys:
            continue
        toks = tuple(tokenize(val))
        other = next(
            (k for k in sorted(keys) if toks in index.attr_values.get(k, {}) and k not in parsed.spec.filters), None
        )
        del parsed.spec.filters[key]
        if other:
            parsed.spec.filters[other] = index.attr_values[other][toks]
        else:
            parsed.spec.desc_tokens.extend(t for t in toks if t not in parsed.spec.desc_tokens)


def _soften_other_kinds_words(parsed: Parsed, index: CatalogueIndex) -> None:
    """Common product words that no product of the named kind has ("office" for formal shoes, "gym" for a water
    bottle) are soft description words, dropped with a note when nothing matches, not hard requirements that
    refuse. A name stays hard: a line name ("LumaPro", in a handful of products) names a product."""
    kind = parsed.spec.filters.get(KIND_KEY)
    words = index.kind_words(kind)
    if not words:
        return
    head = tokenize(kind)[-1]
    # a kind sharing this kind's noun ("school" + "shoes" = Kids School Shoes) means the word changes the kind
    related = {t for toks in index.attr_values.get(KIND_KEY, {}) if toks[-1] == head for t in toks}
    foreign = [
        t
        for t in parsed.spec.type_tokens
        if t not in words and t not in related and index.product_count(t) >= COMMON_WORD_PRODUCTS
    ]
    parsed.spec.type_tokens = [t for t in parsed.spec.type_tokens if t not in foreign]
    parsed.spec.desc_tokens.extend(t for t in foreign if t not in parsed.spec.desc_tokens)


def _detect_fact_intents(parsed: Parsed, tokens: list[str], index: CatalogueIndex, asks: bool) -> None:
    """Price, stock, rating, and attribute questions ("what colour", "kitni moti", "material of P0042")."""
    token_set = set(tokens)
    if token_set & PRICE_W:
        parsed.intents.add("price")
    if token_set & STOCK_W:
        parsed.intents.add("stock")
    if token_set & RATING_W:
        parsed.intents.add("rating")
    asked_words = token_set | {ATTRIBUTE_WORDS[t] for t in token_set if t in ATTRIBUTE_WORDS}  # "moti" -> thickness
    in_names = _words_in_product_names(tokens, index)
    for ktok, keys in index.attr_key_groups.items():
        if ktok not in asked_words or ktok in PRICE_W or ktok in in_names or ktok in index.kind_nouns or not asks:
            continue
        if index.attr_keys.get(ktok) in parsed.spec.filters:  # "blue colour me hai?": colour is given, not asked
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


def _words_in_product_names(tokens: list[str], index: CatalogueIndex) -> set[str]:
    """Words used as part of a product name here: "hair" in "hair dryers", "craft" in "art & craft kits".

    Such a word doesn't ask about the attribute it also names (hair_type, craft_theme); an alias does ("paani").
    """
    return {w for pair in zip(tokens, tokens[1:]) if pair in index.name_pairs for w in pair}


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
        # an attribute's name is not a product word, unless it is also a kind sold ("cable": USB Cables, cable_type)
        named_attribute = tok in index.attr_keys and tok not in index.kind_nouns
        if consumed[i] or tok in IGNORE or named_attribute or tok.isdigit() or len(tok) < 2:
            continue
        if tok not in index.type_vocab and tok not in index.desc_vocab:
            if len(tok) >= 5:
                tok = index.closest(tok) or tok  # typos: "runing"
            if tok not in index.type_vocab and tok not in EXTERNAL_BRANDS:
                fixed = index.closest_short(tok)  # one vowel slip, exactly one product word fits: "jeens", "shert"
                if fixed:
                    parsed.corrected.append((_typed_form(tok, parsed.raw), index.display_word(fixed)))
                    tok = fixed
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
            else:
                for guess in index.suggest(tok)[:1]:  # only asked, never assumed: "sneker" -> did you mean "sneakers"
                    parsed.suggestions.append((_typed_form(tok, parsed.raw), index.display_word(guess)))


def _typed_form(token: str, raw: str) -> str:
    """The word as the shopper typed it ("jeens"), for a reply; the parser only keeps its trimmed form ("jeen")."""
    for word in re.findall(r"[a-z]+", raw.lower()):
        if normalise_word(word) == token:
            return word
    return token


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
    if token_set & (THANKS_W - THANKS_INTENSIFIERS) and not content and not parsed.has_constraint:
        parsed.intents.add("thanks")
    if (account or (token_set & ACCOUNT_W and not parsed.has_product_ref)) and not (token_set & POLICY_W):
        parsed.intents.add("account")
    if not parsed.intents:
        parsed.intents.add("search")
