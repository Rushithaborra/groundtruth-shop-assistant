"""What the assistant returns for one message, and the per-shopper conversation state."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..catalogue import Spec

ANSWER, CLARIFY, REFUSE = "answer", "clarify", "refuse"
CITATION = re.compile(r"\[([^\]]+)\]")


@dataclass
class Response:
    text: str
    action: str
    latency_ms: float = 0.0
    context_tokens: int = 0
    output_tokens: int = 0
    path: str = "rules"  # rules | semantic | llm: which understanding path resolved the query
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
