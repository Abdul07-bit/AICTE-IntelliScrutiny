"""Configurable embedding-model adapter.

Default provider `hashing` is a deterministic, offline feature-hashing embedder (unigrams +
bigrams, signed hashing, L2-normalised). It is lexical rather than semantic and is labelled as
such everywhere it is reported. Swap to `sentence-transformers` or an `http` endpoint via
EMBEDDING_PROVIDER; changing the model marks indexes STALE and requires re-indexing."""
from __future__ import annotations

import hashlib
import math
import re
from functools import lru_cache
from typing import Protocol

from app.core.config import get_settings

_STOP = set("a an the of and or to in for on by with is are be as at this that from it its shall must may".split())


class Embedder(Protocol):
    model_id: str
    dim: int
    kind: str

    def embed(self, texts: list[str]) -> list[list[float]]: ...


def _stem(tok: str) -> str:
    for suf in ("ations", "ation", "ings", "ing", "ies", "es", "s", "ed"):
        if tok.endswith(suf) and len(tok) - len(suf) >= 4:
            return tok[: -len(suf)]
    return tok


class HashingEmbedder:
    kind = "lexical-hashing"

    def __init__(self, dim: int, model_id: str = "local-hashing-v1"):
        self.dim, self.model_id = dim, model_id

    def _features(self, text: str) -> list[str]:
        toks = [_stem(t) for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in _STOP]
        return toks + [f"{a}_{b}" for a, b in zip(toks, toks[1:])]

    def embed(self, texts: list[str]) -> list[list[float]]:
        out = []
        for text in texts:
            vec = [0.0] * self.dim
            for feat in self._features(text):
                h = hashlib.blake2b(feat.encode(), digest_size=8).digest()
                idx = int.from_bytes(h[:4], "little") % self.dim
                sign = 1.0 if h[4] & 1 else -1.0
                vec[idx] += sign * (1.5 if "_" in feat else 1.0)
            norm = math.sqrt(sum(v * v for v in vec)) or 1.0
            out.append([v / norm for v in vec])
        return out


class SentenceTransformerEmbedder:  # pragma: no cover - optional dependency
    kind = "semantic-sentence-transformers"

    def __init__(self, model_id: str, dim: int):
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(model_id)
        self.model_id, self.dim = model_id, dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [list(map(float, v)) for v in self.model.encode(texts, normalize_embeddings=True)]


class HttpEmbedder:  # pragma: no cover - requires an external service
    """POSTs {"model", "input": [...]} and expects {"data": [{"embedding": [...]}]}."""

    kind = "semantic-http"

    def __init__(self, url: str, model_id: str, dim: int):
        self.url, self.model_id, self.dim = url, model_id, dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        import httpx

        r = httpx.post(self.url, json={"model": self.model_id, "input": texts}, timeout=30)
        r.raise_for_status()
        return [d["embedding"] for d in r.json()["data"]]


@lru_cache
def get_embedder() -> Embedder:
    s = get_settings()
    if s.embedding_provider == "sentence-transformers":
        return SentenceTransformerEmbedder(s.embedding_model, s.embedding_dim)
    if s.embedding_provider == "http" and s.embedding_http_url:
        return HttpEmbedder(s.embedding_http_url, s.embedding_model, s.embedding_dim)
    return HashingEmbedder(s.embedding_dim, s.embedding_model)


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))
