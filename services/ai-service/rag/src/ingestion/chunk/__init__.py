"""Expose ingestion chunking adapters.

This package contains business-level adapters that convert loader ``Document``
objects into retrievable ``Chunk`` objects. Low-level text splitting remains in
``src.libs.splitter``.
"""

from src.ingestion.chunk.document_chunker import DocumentChunker

__all__ = ("DocumentChunker",)
