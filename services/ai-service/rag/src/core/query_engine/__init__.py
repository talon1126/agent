"""Expose query preprocessing contracts for the online retrieval pipeline.

The query-engine package owns request normalization and, in later tasks,
Dense/Sparse retrieval, fusion, filtering, and reranking orchestration. Its
public imports remain provider-independent so MCP, CLI, and AImodel adapters do
not depend on concrete LLM or storage implementations.
"""

from src.core.query_engine.dense_route import DenseRoute, DenseTraceContext
from src.core.query_engine.query_processor import (
    ProcessedQuery,
    QueryIntent,
    QueryProcessor,
    QueryRewriter,
)

__all__ = (
    "DenseRoute",
    "DenseTraceContext",
    "ProcessedQuery",
    "QueryIntent",
    "QueryProcessor",
    "QueryRewriter",
)
