"""Last-resort query normaliser using the provided small instruct model.

Runs only when both the rule-based parser and the embedding fallback leave product words unrecognised. The model
turns the messy query into JSON (product type, attribute filters, budget). Every field is then validated against
the catalogue's own vocabulary and the query's own numbers; anything that doesn't validate is dropped. The model
never writes reply text, so it cannot introduce a price, product or policy claim.

Residual risk (documented, not solved): a validated but wrong product type ("charger" -> "power bank" is fine,
"iPhone case" -> "wallet" is not). Words the model couldn't explain stay flagged, so the reply still says
"I can't verify ...", and a query the model marks as out-of-catalogue is not mapped at all.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ..config import get_settings
from .calls import ModelCallError
from .semantic import type_words

if TYPE_CHECKING:
    from ..catalogue import CatalogueIndex
    from ..understanding import Parsed

# Kept in a file so the prompt can be reviewed and changed without touching code; read verbatim.
SYSTEM = (Path(__file__).parent / "prompts" / "normaliser_system.txt").read_text(encoding="utf-8")

_JSON = re.compile(r"\{.*\}", re.S)
_AMOUNT = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(k|thousand|hazar)?", re.I)

Generate = Callable[[str, str], tuple[str, int, int]]  # (system, user) -> (text, input_tokens, output_tokens)


class LLMNormaliser:
    def __init__(self, generate: Generate, max_values_per_attribute: int | None = None) -> None:
        self.generate = generate
        self.max_values = (max_values_per_attribute if max_values_per_attribute is not None
                           else get_settings().models.llm_max_values_per_attribute)
        self._schema_cache: dict[int, tuple[str, list[str]]] = {}

    def _schema(self, index: CatalogueIndex) -> tuple[str, list[str]]:
        if id(index) not in self._schema_cache:
            types = type_words(index)
            attrs = {k: sorted(v.values())[: self.max_values] for k, v in index.attr_values.items()}
            text = f"ALLOWED_TYPES: {json.dumps(types)}\nALLOWED_ATTRIBUTES: {json.dumps(attrs, ensure_ascii=False)}"
            self._schema_cache[id(index)] = (text, types)
        return self._schema_cache[id(index)]

    @staticmethod
    def _query_amounts(raw: str) -> set[float]:
        return {float(n.replace(",", "")) * (1000 if unit else 1) for n, unit in _AMOUNT.findall(raw)}

    @staticmethod
    def parse(text: str) -> dict | None:
        """The first JSON object in the model's text, or None."""
        text = re.sub(r"```(?:json)?", "", text)
        m = _JSON.search(text)
        if not m:
            return None
        try:
            data = json.loads(m.group())
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    def apply(self, parsed: Parsed, index: CatalogueIndex) -> tuple[int, int]:
        """Validate the model's reading and merge it into `parsed`. Returns (input_tokens, output_tokens)."""
        schema, types = self._schema(index)
        try:
            text, n_in, n_out = self.generate(SYSTEM, f"{schema}\n\nSHOPPER: {parsed.raw}\nJSON:")
        except ModelCallError:  # slow or failing model (already logged): fall back to the rules' answer
            return (0, 0)
        data = self.parse(text) or {}
        flagged = {str(w).lower() for w in data.get("out_of_catalogue") or [] if isinstance(w, str)}
        if flagged & set(parsed.unknown):
            return (n_in, n_out)  # the model agrees these words aren't sold: keep the refusal path
        ptype = data.get("product_type")
        if isinstance(ptype, str) and ptype.lower() in types:  # validated; recorded even if the rules agreed
            if ptype.lower() not in parsed.spec.type_tokens:
                parsed.spec.type_tokens.append(ptype.lower())
            parsed.mapped.append((" ".join(parsed.unknown) or parsed.raw, ptype.lower()))
        filters = data.get("filters") if isinstance(data.get("filters"), dict) else {}
        for key, value in filters.items():
            if not isinstance(value, str) or key not in index.attr_values or key in parsed.spec.filters:
                continue
            match = next((orig for orig in index.attr_values[key].values() if orig.lower() == value.lower()), None)
            if match is not None:
                parsed.spec.filters[key] = match
        allowed = self._query_amounts(parsed.raw)  # a budget must come from the shopper's own numbers
        for field in ("price_min", "price_max"):
            value = data.get(field)
            if isinstance(value, (int, float)) and float(value) in allowed and getattr(parsed.spec, field) is None:
                setattr(parsed.spec, field, float(value))
        return (n_in, n_out)
