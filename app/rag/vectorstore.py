"""Chroma vector store wiring.

Uses Chroma's built-in ONNX MiniLM embedding function (no torch / external
API key required), so RAG works fully offline once the small embedding model
is cached on first run.
"""
from __future__ import annotations

import chromadb
from chromadb.utils import embedding_functions

from app.config import settings

COLLECTION_NAME = "knowledge_base"

_client: chromadb.ClientAPI | None = None


def get_client() -> chromadb.ClientAPI:
    global _client
    if _client is None:
        settings.vector_db_path.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(
            path=str(settings.vector_db_path),
            settings=chromadb.config.Settings(anonymized_telemetry=False),
        )
    return _client


def get_collection():
    client = get_client()
    embedding_fn = embedding_functions.DefaultEmbeddingFunction()
    return client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=embedding_fn,
        metadata={"hnsw:space": "cosine"},
    )


def reset_collection():
    client = get_client()
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    return get_collection()
