"""
Embedding wrapper around fastembed — runs fully locally via ONNX, no API
key and no per-call cost. Model weights (~130MB for the default) download
once on first use and are cached under ~/.cache/fastembed.
"""

from __future__ import annotations
from fastembed import TextEmbedding

# bge-small is a good default: small, fast on CPU, strong retrieval quality
# for its size. Swap to "BAAI/bge-base-en-v1.5" for better quality at the
# cost of a larger download and slower embedding.
DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"

_model_cache: dict[str, TextEmbedding] = {}


def get_embedder(model_name: str = DEFAULT_MODEL) -> TextEmbedding:
    if model_name not in _model_cache:
        print(f"[embedder] loading model '{model_name}' (first run downloads weights)...")
        _model_cache[model_name] = TextEmbedding(model_name=model_name)
    return _model_cache[model_name]


def embed_texts(texts: list[str], model_name: str = DEFAULT_MODEL) -> list[list[float]]:
    """Embeds a batch of document chunks. Returns one vector per input text, same order."""
    if not texts:
        return []
    model = get_embedder(model_name)
    # bge models are trained with a query/passage distinction — passages get no prefix
    return [vec.tolist() for vec in model.embed(texts)]


def embed_query(query: str, model_name: str = DEFAULT_MODEL) -> list[float]:
    """Embeds a single search query. bge models expect a specific prefix for queries."""
    model = get_embedder(model_name)
    prefixed = f"query: {query}"
    return list(model.query_embed(prefixed))[0].tolist()
