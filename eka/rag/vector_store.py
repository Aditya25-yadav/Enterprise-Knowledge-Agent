"""
Vector store wrapper around ChromaDB, running fully local with on-disk
persistence — no hosted service, no API key.
"""

from __future__ import annotations
import chromadb
from chromadb.config import Settings

DEFAULT_PERSIST_DIR = "./chroma_db"
COLLECTION_NAME = "enterprise_knowledge"


def get_collection(persist_dir: str = DEFAULT_PERSIST_DIR):
    client = chromadb.PersistentClient(path=persist_dir, settings=Settings(anonymized_telemetry=False))
    return client.get_or_create_collection(name=COLLECTION_NAME, metadata={"hnsw:space": "cosine"})


def upsert_chunks(chunks: list[dict], embeddings: list[list[float]], persist_dir: str = DEFAULT_PERSIST_DIR):
    """
    Writes chunks + their embeddings into the vector store. Chroma metadata
    values must be str/int/float/bool, so permissions (a list of dicts) are
    dropped here for now — full permission-aware filtering comes back once
    OKF is layered in. Everything needed for citations is kept.
    """
    if not chunks:
        return

    collection = get_collection(persist_dir)

    ids = [c["chunk_id"] for c in chunks]
    documents = [c["text"] for c in chunks]
    metadatas = [
        {
            "document_id": c["document_id"],
            "source_system": c["source_system"],
            "title": c["title"],
            "url": c.get("url") or "",
            "author": c.get("author") or "",
            "chunk_index": c["chunk_index"],
        }
        for c in chunks
    ]

    collection.upsert(ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas)
    print(f"[vector_store] upserted {len(chunks)} chunk(s) into '{COLLECTION_NAME}'")


def query(query_embedding: list[float], top_k: int = 5, persist_dir: str = DEFAULT_PERSIST_DIR) -> list[dict]:
    """Returns the top_k most similar chunks, each with its text, metadata, and similarity score."""
    collection = get_collection(persist_dir)
    if collection.count() == 0:
        return []

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=min(top_k, collection.count()),
        include=["documents", "metadatas", "distances"],
    )

    hits = []
    for doc, meta, dist in zip(results["documents"][0], results["metadatas"][0], results["distances"][0]):
        hits.append({
            "text": doc,
            "metadata": meta,
            "score": 1 - dist,  # cosine distance -> similarity
        })
    return hits
