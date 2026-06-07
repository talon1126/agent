"""Expose response-layer contracts for citations and later answer assembly.

The response package converts ranked retrieval results into source-grounded
objects consumed by MCP, AImodel, CLI, and Dashboard adapters. It does not run
retrieval, reranking, or model generation.
"""

from src.core.response.citation_builder import CitationBuilder
from src.core.types import Citation

__all__ = (
    "Citation",
    "CitationBuilder",
)
