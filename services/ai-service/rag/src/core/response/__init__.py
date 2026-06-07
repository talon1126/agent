"""Expose response-layer contracts for public knowledge-hub assembly.

The response package converts ranked retrieval results into source-grounded
text, citations, and optional image references consumed by MCP, AImodel, CLI,
and Dashboard adapters. It does not run retrieval, reranking, model generation,
or transport serialization.
"""

from src.core.response.citation_builder import CitationBuilder
from src.core.response.multimodal_assembler import (
    MultimodalAssembler,
    ResponseImage,
)
from src.core.response.response_builder import (
    KnowledgeHubResponse,
    KnowledgeHubResponseBuilder,
)
from src.core.types import Citation

__all__ = (
    "Citation",
    "CitationBuilder",
    "KnowledgeHubResponse",
    "KnowledgeHubResponseBuilder",
    "MultimodalAssembler",
    "ResponseImage",
)
