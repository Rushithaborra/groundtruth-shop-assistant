"""Loaders for the provided offline models: the MiniLM embedding model and the Qwen2.5 GGUF instruct model.

Imported only when a model path is configured, so the rule-based assistant needs no ML libraries. Both load from
local files only: the embedding weights are safetensors and no repository code is run (trust_remote_code stays off);
the instruct model is a GGUF file read by llama.cpp. Every call goes through `call_model` (timeout, retry, log).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ..config import get_settings
from ..errors import GroundTruthError
from .calls import call_model
from .semantic import unit


class ModelLoadError(GroundTruthError, RuntimeError):
    """A model or the library that runs it is missing."""


def _missing_library(name: str) -> ModelLoadError:
    """The error for an optional model library that isn't installed."""
    return ModelLoadError(
        f"{name} is not installed; the model libraries are optional and installed by hand (see README, 'How to run')"
    )


class LocalEmbedder:
    """The provided sentence-embedding model, on CPU, with unit-length vectors."""

    def __init__(self, model_path: str | Path) -> None:
        self.name = str(model_path)
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise _missing_library("sentence-transformers") from exc
        if not Path(model_path).is_dir():  # local files only: a missing folder is never looked up online
            raise ModelLoadError(
                f"embedding model folder not found: {model_path} "
                "(download it with the pack's download_models.py, see README, 'How to run')"
            )
        settings = get_settings().models
        try:
            self._model = SentenceTransformer(str(model_path), device="cpu", local_files_only=True)
        except (OSError, ValueError) as exc:  # a folder that is not a model, or a cut-off download
            raise ModelLoadError(f"embedding model in {model_path} could not be loaded: {exc}") from exc
        self._model.max_seq_length = settings.embed_max_length
        self._timeout_s, self._retries, self._backoff_s = (
            settings.embed_timeout_s,
            settings.model_retries,
            settings.model_backoff_s,
        )

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        """Unit-length vectors for the texts (through call_model)."""
        batch = list(texts)
        vectors = call_model(
            lambda: self._model.encode(batch, normalize_embeddings=True).tolist(),
            what=f"embedding {len(batch)} text(s)",
            timeout_s=self._timeout_s * max(1, len(batch) // 64 + 1),  # start-up encodes a few hundred texts at once
            retries=self._retries,
            backoff_s=self._backoff_s,
        )
        return [unit(v) for v in vectors]


def _gguf_file(model_path: str | Path) -> Path:
    """The .gguf model file, given the file or the folder holding it."""
    path = Path(model_path)
    if path.is_dir():
        files = sorted(path.glob("*.gguf"))
        if not files:
            raise ModelLoadError(f"no .gguf model file in {path}")
        return files[0]
    if not path.is_file():
        raise ModelLoadError(f"instruct model not found: {path}")
    return path


class LocalGenerator:
    """The provided instruct model (GGUF, run by llama.cpp on CPU): greedy JSON output and real token counts."""

    def __init__(self, model_path: str | Path) -> None:
        try:
            from llama_cpp import Llama
        except ImportError as exc:
            raise _missing_library("llama-cpp-python") from exc
        settings = get_settings().models
        self.name = str(model_path)
        self._llm = Llama(
            model_path=str(_gguf_file(model_path)), n_ctx=settings.llm_context_tokens, seed=0, verbose=False
        )
        self._settings = settings

    def __call__(
        self, system: str, user: str, max_tokens: int | None = None, json_mode: bool = True
    ) -> tuple[str, int, int]:
        """(reply text, input tokens, output tokens) for one system + user message pair. `max_tokens` caps this
        call's output (a menu pick needs 4, not the configured 120); `json_mode=False` allows plain text."""
        s = self._settings
        extra = {"response_format": {"type": "json_object"}} if json_mode else {}
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        out = call_model(
            lambda: self._llm.create_chat_completion(
                messages=messages,
                temperature=s.llm_temperature,
                max_tokens=max_tokens or s.llm_max_new_tokens,
                **extra,
            ),
            what="instruct model",
            timeout_s=s.llm_timeout_s,
            retries=s.model_retries,
            backoff_s=s.model_backoff_s,
        )
        usage = out.get("usage") or {}
        text = out["choices"][0]["message"].get("content") or ""
        return text, int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
