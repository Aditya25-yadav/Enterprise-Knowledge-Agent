"""
CLI entry point for the RAG pipeline.

Usage:
  python main.py ingest [--folder-id FOLDER_ID]
  python main.py query "your question here"
"""

from __future__ import annotations
import sys
import argparse
from dotenv import load_dotenv

load_dotenv()

from connectors.google_drive.connector import ingest as drive_ingest
from rag.embedder import embed_texts, embed_query
from rag.vector_store import upsert_chunks, query as vector_query
from rag.generator import generate_answer


def run_ingest(folder_id: str | None):
    print("=== Ingesting from Google Drive ===")
    chunks = drive_ingest(folder_id=folder_id)
    if not chunks:
        print("No chunks produced — nothing to index.")
        return

    print(f"=== Embedding {len(chunks)} chunk(s) ===")
    texts = [c["text"] for c in chunks]
    embeddings = embed_texts(texts)

    print("=== Writing to vector store ===")
    upsert_chunks(chunks, embeddings)

    print("=== Done ===")


def run_query(question: str, top_k: int = 5):
    print(f"=== Retrieving top {top_k} chunks for: \"{question}\" ===")
    q_embedding = embed_query(question)
    hits = vector_query(q_embedding, top_k=top_k)

    if not hits:
        print("No indexed documents found. Run `python main.py ingest` first.")
        return

    for h in hits:
        print(f"  - [{h['score']:.3f}] {h['metadata']['title']}")

    print("\n=== Generating answer ===")
    result = generate_answer(question, hits)

    print("\n" + "=" * 60)
    print(result["answer"])
    print("=" * 60)
    print("\nSources:")
    for s in result["sources"]:
        print(f"  [{s['index']}] {s['title']} ({s['source_system']}) — {s['url']}")


def main():
    parser = argparse.ArgumentParser(description="Enterprise Knowledge Agent — RAG CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser("ingest", help="Fetch and index files from Google Drive")
    ingest_parser.add_argument("--folder-id", default=None, help="Optional Drive folder ID to scope ingestion to")

    query_parser = subparsers.add_parser("query", help="Ask a question against the indexed knowledge base")
    query_parser.add_argument("question", type=str)
    query_parser.add_argument("--top-k", type=int, default=5)

    args = parser.parse_args()

    if args.command == "ingest":
        run_ingest(args.folder_id)
    elif args.command == "query":
        run_query(args.question, args.top_k)


if __name__ == "__main__":
    main()
