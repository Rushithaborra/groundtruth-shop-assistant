"""Assemble the assistant from the configured catalogue, policy and optional model fallbacks."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .answering import ShoppingAssistant
from .catalogue import CatalogueIndex, load_catalogue
from .config import get_settings
from .errors import DataFileError
from .policy import Section, load_policy

if TYPE_CHECKING:  # only for annotations: the model modules are imported lazily, when a model is used
    from .models.normaliser import LLMNormaliser
    from .models.semantic import SemanticFallback

logger = logging.getLogger(__name__)


def build_assistant(
    catalogue_path: str | Path,
    policy_path: str | Path,
    embed_model: str | None = None,
    semantic_config: str | Path | None = None,
    llm_model: str | None = None,
    cache_dir: str | Path | None = None,
    **kwargs: Any,
) -> ShoppingAssistant:
    """Build the assistant. The rule-based path always runs first; the optional fallbacks only see its misses.

    embed_model      path to the provided embedding model; enables the embedding fallback only if
                     `semantic_config` exists (thresholds written by `python main.py calibrate`)
    llm_model        path to the provided instruct model; enables the last-resort query normaliser
    """
    paths = get_settings().paths
    products = load_catalogue(catalogue_path)
    if not products:
        raise DataFileError(f"no products could be read from {catalogue_path}")
    index, policy = CatalogueIndex(products), load_policy(policy_path)
    if not policy:
        logger.warning("the policy file %s has no sections; policy questions will be refused", policy_path)
    if embed_model:
        semantic = _semantic_fallback(
            index, policy, embed_model, semantic_config or paths.semantic_config, cache_dir or paths.cache_dir
        )
        if semantic:
            kwargs["semantic"] = semantic
    if llm_model:
        kwargs["normaliser"] = _normaliser(llm_model)
    assistant = ShoppingAssistant(index, policy, **kwargs)
    assistant.warm_up()
    return assistant


def _semantic_fallback(
    index: CatalogueIndex, policy: list[Section], embed_model: str, semantic_config: str | Path, cache_dir: str | Path
) -> SemanticFallback | None:
    """The embedding fallback, or None (with a warning) when it has not been calibrated yet."""
    from .models.local import LocalEmbedder
    from .models.semantic import SemanticFallback, load_config

    config = load_config(semantic_config)
    if config is None:
        logger.warning(
            "%s not found: run `python main.py calibrate` first; embedding fallback disabled", semantic_config
        )
        return None
    return SemanticFallback(
        index,
        policy,
        LocalEmbedder(embed_model),
        config["map_threshold"],
        config.get("alt_threshold"),
        config.get("policy_threshold"),
        cache_dir=cache_dir,
    )


def _normaliser(llm_model: str) -> LLMNormaliser:
    """The last-resort query normaliser around the provided instruct model."""
    from .models.local import LocalGenerator
    from .models.normaliser import LLMNormaliser

    return LLMNormaliser(LocalGenerator(llm_model))
