"""Assemble the assistant from the configured catalogue, policy and optional model fallbacks."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .answering import ShoppingAssistant
from .catalogue import CatalogueIndex, Product, load_catalogue
from .config import get_settings
from .errors import DataFileError
from .policy import Section, load_policy
from .synonyms import LearnedSynonyms

if TYPE_CHECKING:  # only for annotations: the model modules are imported lazily, when a model is used
    from .models.normaliser import LLMNormaliser
    from .models.resolver import ShortlistNormaliser
    from .models.semantic import SemanticFallback

logger = logging.getLogger(__name__)


def build_assistant(
    catalogue_path: str | Path,
    policy_path: str | Path,
    embed_model: str | None = None,
    semantic_config: str | Path | None = None,
    llm_model: str | None = None,
    cache_dir: str | Path | None = None,
    strict: bool | None = None,
    resolver: str = "shortlist",
    **kwargs: Any,
) -> ShoppingAssistant:
    """Build the assistant. The rule-based path always runs first; the optional fallbacks only see its misses.

    embed_model      path to the provided embedding model; enables the embedding fallback only if
                     `semantic_config` exists (thresholds written by `python main.py calibrate`)
    llm_model        path to the provided instruct model; enables the last-resort query normaliser
    resolver         how the instruct model is asked: "shortlist" (a numbered menu, ~100 tokens) or "schema" (the whole
                     catalogue vocabulary in the prompt, ~3,800 tokens)
    strict           reject bad or duplicate catalogue rows instead of skipping them (default: only for the pack's)
    """
    paths = get_settings().paths
    products = _load_products(catalogue_path, strict)
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
        kwargs["normaliser"] = _normaliser(llm_model, resolver)
    kwargs.setdefault("synonyms", LearnedSynonyms(paths.synonyms))
    assistant = ShoppingAssistant(index, policy, **kwargs)
    assistant.warm_up()
    return assistant


def _load_products(catalogue_path: str | Path, strict: bool | None) -> list[Product]:
    """Load the catalogue. The organisers' catalogue is the single source of truth, so it is loaded strictly: a bad row,
    a duplicate id or a wrong product count stops the run instead of silently dropping products."""
    settings = get_settings()
    is_pack = Path(catalogue_path).resolve() == settings.paths.pack_catalogue.resolve()
    strict = (is_pack and settings.catalogue.pack_strict) if strict is None else strict
    try:
        products = load_catalogue(catalogue_path, strict=strict)
    except DataFileError:
        raise
    except ValueError as exc:  # strict loading names the bad row
        raise DataFileError(f"{catalogue_path}: {exc}") from exc
    if not products:
        raise DataFileError(f"no products could be read from {catalogue_path}")
    expected = settings.catalogue.pack_expected_products
    if is_pack and strict and expected is not None and len(products) != expected:
        raise DataFileError(
            f"the pack catalogue holds {len(products)} products, expected {expected} "
            "(set catalogue.pack_expected_products in config.json if that is right)"
        )
    return products


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


def _normaliser(llm_model: str, resolver: str = "shortlist") -> LLMNormaliser | ShortlistNormaliser:
    """The last-resort query normaliser around the provided instruct model."""
    from .models.local import LocalGenerator

    generate = LocalGenerator(llm_model)
    if resolver == "schema":
        from .models.normaliser import LLMNormaliser

        return LLMNormaliser(generate)
    from .models.resolver import ShortlistNormaliser

    return ShortlistNormaliser(generate)
