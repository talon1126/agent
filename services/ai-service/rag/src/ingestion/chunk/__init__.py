"""Expose ingestion chunking adapters.

This package contains business-level adapters that convert loader ``Document``
objects into retrievable ``Chunk`` objects. Low-level text splitting remains in
``src.libs.splitter``.
"""

from src.ingestion.chunk.chunk_id import build_chunk_id
from src.ingestion.chunk.document_chunker import DocumentChunker
from src.ingestion.chunk.splitter_step import SplitterStep

__all__ = ("DocumentChunker", "SplitterStep", "build_chunk_id")
