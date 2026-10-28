"""Loaders for the provided offline models: the embedding model and the small instruct model.

Imported only when a model path is configured, so the rule-based assistant needs no ML libraries.
"""
from __future__ import annotations

from typing import Sequence

from ..config import get_settings
from .semantic import unit


class LocalEmbedder:
    """The provided offline embedding model. Tries sentence-transformers, then plain transformers (mean pooling)."""

    def __init__(self, model_path: str, max_length: int | None = None):
        self.name = str(model_path)
        max_length = max_length if max_length is not None else get_settings().models.embed_max_length
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
            model = SentenceTransformer(model_path, device="cpu")
            self._encode = lambda texts: model.encode(list(texts), normalize_embeddings=True).tolist()
            return
        except ImportError:
            pass
        import torch  # type: ignore
        from transformers import AutoModel, AutoTokenizer  # type: ignore
        tok = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
        model = AutoModel.from_pretrained(model_path, local_files_only=True).eval()

        def encode(texts):
            with torch.no_grad():
                batch = tok(list(texts), padding=True, truncation=True, max_length=max_length, return_tensors="pt")
                out = model(**batch).last_hidden_state
                mask = batch["attention_mask"].unsqueeze(-1).float()
                vecs = (out * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
            return [unit(v) for v in vecs.tolist()]
        self._encode = encode

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        return [unit(v) for v in self._encode(texts)]


class LocalGenerator:
    """The provided offline instruct model via transformers, greedy decoding, real token counts."""

    def __init__(self, model_path: str, max_new_tokens: int | None = None):
        import torch  # type: ignore
        from transformers import AutoModelForCausalLM, AutoTokenizer  # type: ignore
        self.torch = torch
        self.tok = AutoTokenizer.from_pretrained(model_path, local_files_only=True)
        self.model = AutoModelForCausalLM.from_pretrained(model_path, local_files_only=True).eval()
        self.max_new_tokens = max_new_tokens if max_new_tokens is not None else get_settings().models.llm_max_new_tokens

    def __call__(self, system: str, user: str) -> tuple[str, int, int]:
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        if getattr(self.tok, "chat_template", None):
            ids = self.tok.apply_chat_template(msgs, add_generation_prompt=True, return_tensors="pt")
        else:
            ids = self.tok(f"{system}\n\n{user}", return_tensors="pt").input_ids
        with self.torch.no_grad():
            out = self.model.generate(ids, max_new_tokens=self.max_new_tokens, do_sample=False,
                                      pad_token_id=self.tok.eos_token_id)
        new = out[0][ids.shape[1]:]
        return self.tok.decode(new, skip_special_tokens=True), int(ids.shape[1]), int(len(new))
