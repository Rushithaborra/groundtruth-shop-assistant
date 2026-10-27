"""Word-boundary repair against the catalogue vocabulary."""
from __future__ import annotations

from ..catalogue import CatalogueIndex


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
