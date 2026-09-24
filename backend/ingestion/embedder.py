"""
Local Embedding Generator for Enterprise Knowledge Ingestion.

Uses sentence-transformers to generate dense vector embeddings locally in-process.
Runs offline with zero API cost and no external network dependency.

Default model: Qwen/Qwen3-Embedding-0.6B (1024 dimensions)
"""

from __future__ import annotations

import os
from typing import Any, List, Optional, Tuple

from backend.ingestion.chunk import SmartChunk


class LocalEmbedder:
    """
    In-process local embedding generator using sentence-transformers.
    """

    DEFAULT_MODEL = "Qwen/Qwen3-Embedding-0.6B"

    def __init__(
        self,
        model_name: Optional[str] = None,
        device: Optional[str] = None,
    ) -> None:
        self.model_name = model_name or os.getenv("EMBEDDING_MODEL", self.DEFAULT_MODEL)
        self.device = device  # None = auto-detect ('mps' on Apple Silicon, 'cuda' on GPU, 'cpu')
        self._model = None

    def _get_model(self) -> Any:
        """Lazy loads the SentenceTransformer model on first invocation."""
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self.model_name, device=self.device)
        return self._model

    @property
    def dimension(self) -> int:
        """Returns the embedding vector dimension for this model (e.g. 768 for bge-base)."""
        model = self._get_model()
        if hasattr(model, "get_embedding_dimension"):
            return model.get_embedding_dimension()
        return model.get_sentence_embedding_dimension()

    def embed_text(self, text: str) -> List[float]:
        """
        Generates embedding for a single text query or string.
        """
        if not text or not text.strip():
            return [0.0] * self.dimension
        model = self._get_model()
        vec = model.encode(text, normalize_embeddings=True)
        return vec.tolist()

    def embed_texts(self, texts: List[str], batch_size: int = 32) -> List[List[float]]:
        """
        Generates embeddings for a batch of text strings.
        """
        if not texts:
            return []
        model = self._get_model()
        vecs = model.encode(texts, batch_size=batch_size, normalize_embeddings=True, show_progress_bar=False)
        return vecs.tolist()

    def format_chunk_for_embedding(self, chunk: SmartChunk) -> str:
        """
        Builds a context-enriched text representation specifically for vector embedding.
        Prepends Document Title, Breadcrumb Hierarchy, and Step Info to maximize semantic density.
        """
        parts = []
        if chunk.title:
            parts.append(f"Title: {chunk.title}")
        if chunk.section_path:
            parts.append(f"Section: {' > '.join(chunk.section_path)}")
        elif chunk.section_heading:
            parts.append(f"Section: {chunk.section_heading}")
        if chunk.sequence:
            parts.append(f"Step: {chunk.sequence.step}/{chunk.sequence.total_steps} ({chunk.sequence.step_title or ''})")
        if chunk.source:
            parts.append(f"Source: {chunk.source.upper()}")
        parts.append(f"Content:\n{chunk.text}")
        return "\n".join(parts)

    def embed_chunks(
        self,
        chunks: List[SmartChunk],
        batch_size: int = 32,
    ) -> List[Tuple[SmartChunk, List[float]]]:
        """
        Generates vector embeddings for a list of SmartChunk objects.
        Returns a list of (SmartChunk, vector) pairs.
        """
        if not chunks:
            return []

        # Prepare context-enriched texts for embedding (including title, breadcrumbs, and step info)
        embedding_inputs = [self.format_chunk_for_embedding(c) for c in chunks]
        vectors = self.embed_texts(embedding_inputs, batch_size=batch_size)

        return list(zip(chunks, vectors))
