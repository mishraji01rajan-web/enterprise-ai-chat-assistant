"""Lightweight markdown-aware chunker (no extra dependency needed).

Splits on paragraph boundaries first, then greedily packs paragraphs into
chunks up to `chunk_size` characters with `overlap` characters of trailing
context carried into the next chunk, so a fact split across a paragraph
boundary still has surrounding context in at least one chunk.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Chunk:
    text: str
    index: int


def chunk_text(text: str, chunk_size: int = 800, overlap: int = 120) -> list[Chunk]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return []

    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        candidate = f"{current}\n\n{para}" if current else para
        if len(candidate) <= chunk_size:
            current = candidate
            continue
        if current:
            chunks.append(current)
            tail = current[-overlap:] if overlap and len(current) > overlap else current
            current = f"{tail}\n\n{para}"
        else:
            # single paragraph longer than chunk_size: hard-split it
            for i in range(0, len(para), chunk_size):
                chunks.append(para[i : i + chunk_size])
            current = ""
    if current:
        chunks.append(current)

    return [Chunk(text=c, index=i) for i, c in enumerate(chunks)]
