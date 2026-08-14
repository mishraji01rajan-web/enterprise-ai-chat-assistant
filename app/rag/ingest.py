"""Document ingestion: load knowledge_base/*.md -> chunk -> embed -> store.

Re-runnable via `python -m app.rag.ingest`. Extracts a doc_id/title/category
from each file's header block so citations can point back to a stable
identifier (e.g. "POL-001") rather than just a filename.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.rag.chunking import chunk_text
from app.rag.vectorstore import reset_collection

DOC_ID_RE = re.compile(r"\*\*Document ID:\*\*\s*(\S+)")
CATEGORY_RE = re.compile(r"\*\*Category:\*\*\s*(.+)")
TITLE_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)


@dataclass
class ParsedDoc:
    path: Path
    doc_id: str
    title: str
    category: str
    text: str


def parse_document(path: Path) -> ParsedDoc:
    text = path.read_text(encoding="utf-8")
    doc_id_match = DOC_ID_RE.search(text)
    category_match = CATEGORY_RE.search(text)
    title_match = TITLE_RE.search(text)
    return ParsedDoc(
        path=path,
        doc_id=doc_id_match.group(1) if doc_id_match else path.stem.upper(),
        title=title_match.group(1).strip() if title_match else path.stem,
        category=category_match.group(1).strip() if category_match else "General",
        text=text,
    )


def ingest_all(kb_dir: Path | None = None) -> int:
    kb_dir = kb_dir or settings.knowledge_base_dir
    md_files = sorted(kb_dir.glob("*.md"))
    if not md_files:
        raise FileNotFoundError(f"No markdown documents found in {kb_dir}")

    collection = reset_collection()

    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict] = []

    for path in md_files:
        doc = parse_document(path)
        chunks = chunk_text(doc.text, chunk_size=settings.rag_chunk_size, overlap=settings.rag_chunk_overlap)
        for chunk in chunks:
            chunk_id = f"{doc.doc_id}-{chunk.index}"
            ids.append(chunk_id)
            documents.append(chunk.text)
            metadatas.append(
                {
                    "doc_id": doc.doc_id,
                    "title": doc.title,
                    "category": doc.category,
                    "source_file": path.name,
                    "chunk_index": chunk.index,
                }
            )

    collection.add(ids=ids, documents=documents, metadatas=metadatas)
    return len(ids)


if __name__ == "__main__":
    count = ingest_all()
    print(f"Ingested {count} chunks from knowledge_base/ into Chroma at {settings.vector_db_path}")
