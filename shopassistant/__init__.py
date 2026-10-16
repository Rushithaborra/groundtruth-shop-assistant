from __future__ import annotations

import sys

from .assistant import ANSWER, CLARIFY, REFUSE, Response, Session, ShoppingAssistant
from .catalogue import Product, load_catalogue
from .index import CatalogueIndex
from .policy import Section, load_policy
from .sessions import SessionStore


def build_assistant(catalogue_path: str, policy_path: str, embed_model: str | None = None,
                    semantic_config: str = "semantic_config.json", llm_model: str | None = None,
                    cache_dir: str = ".cache", **kwargs) -> ShoppingAssistant:
    """Build the assistant. The rule-based path always runs first; the optional fallbacks only see its misses.

    embed_model      path to the provided embedding model; enables the embedding fallback only if
                     `semantic_config` exists (thresholds written by calibrate_semantic.py)
    llm_model        path to the provided instruct model; enables the last-resort query normaliser
    """
    index, policy = CatalogueIndex(load_catalogue(catalogue_path)), load_policy(policy_path)
    if embed_model:
        from .semantic import LocalEmbedder, SemanticFallback, load_config
        config = load_config(semantic_config)
        if config is None:
            print(f"[semantic] {semantic_config} not found: run calibrate_semantic.py first; fallback disabled",
                  file=sys.stderr)
        else:
            kwargs["semantic"] = SemanticFallback(index, policy, LocalEmbedder(embed_model), config["map_threshold"],
                                                  config.get("alt_threshold"), config.get("policy_threshold"),
                                                  cache_dir=cache_dir)
    if llm_model:
        from .llm_normaliser import LLMNormaliser, LocalGenerator
        kwargs["normaliser"] = LLMNormaliser(LocalGenerator(llm_model))
    assistant = ShoppingAssistant(index, policy, **kwargs)
    assistant.warm_up()
    return assistant


__all__ = ["ANSWER", "CLARIFY", "REFUSE", "Response", "Session", "ShoppingAssistant", "Product",
           "CatalogueIndex", "Section", "SessionStore", "load_catalogue", "load_policy", "build_assistant"]
