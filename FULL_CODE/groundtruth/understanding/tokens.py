"""Word-boundary repair against the catalogue vocabulary."""

from __future__ import annotations

import re

from ..catalogue import CatalogueIndex
from ..text import normalise_word


def repair_tokens(tokens: list[str], index: CatalogueIndex) -> list[str]:
    """Fix word-boundary mistakes: "smart watch" -> smartwatch, "powerbank" -> power bank."""
    vocab = index.vocab
    out, i = [], 0
    while i < len(tokens):
        tok = tokens[i]
        if i + 1 < len(tokens) and tokens[i] + tokens[i + 1] in vocab and not (tok in vocab and tokens[i + 1] in vocab):
            out.append(tok + tokens[i + 1])
            i += 2
            continue
        if tok not in vocab and len(tok) >= 6 and not tok.isdigit():
            for k in range(3, len(tok) - 2):
                if tok[:k] in vocab and tok[k:] in vocab:
                    out += [tok[:k], tok[k:]]
                    break
            else:
                out.append(tok)
        else:
            out.append(tok)
        i += 1
    return out


def join_split_names(text: str, index: CatalogueIndex) -> str:
    """Rejoin a product-line name typed as two words, before stemming can change them ("nimbus air" -> "nimbusair";
    stemming alone would turn "nimbus" into "nimbu" and the pair would no longer match)."""
    words = re.findall(r"[a-z0-9]+|\S", text)
    out, i = [], 0
    while i < len(words):
        a = words[i]
        b = words[i + 1] if i + 1 < len(words) else ""
        joined = normalise_word(a + b) if a.isalpha() and b.isalpha() else ""
        if (
            joined
            and joined in index.vocab
            and not (normalise_word(a) in index.vocab and normalise_word(b) in index.vocab)
        ):
            out.append(a + b)
            i += 2
            continue
        out.append(a)
        i += 1
    return " ".join(out)
