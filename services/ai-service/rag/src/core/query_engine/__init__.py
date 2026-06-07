"""Expose query preprocessing contracts for the online retrieval pipeline.

The query-engine package owns request normalization and, in later tasks,
Dense/Sparse retrieval, fusion, filtering, and reranking orchestration. Its
public imports remain provider-independent so MCP, CLI, and AImodel adapters do
not depend on concrete LLM or storage implementations.
"""

from src.core.query_engine.dense_route import DenseRoute, DenseTraceContext
from src.core.query_engine.fusion import reciprocal_rank_fusion
from src.core.query_engine.hybrid_engine import (
    CandidateFilter,
    CandidateFilterReport,
    HybridSearch,
    HybridSearchResult,
    HybridTraceContext,
)
from src.core.query_engine.query_processor import (
    ProcessedQuery,
    QueryIntent,
    QueryProcessor,
    QueryRewriter,
)
from src.core.query_engine.reranker import (
    RerankController,
    RerankOutcome,
    RerankTraceContext,
)
from src.core.query_engine.sparse_route import SparseRoute, SparseTraceContext

__all__ = (
    "DenseRoute",
    "DenseTraceContext",
    "CandidateFilter",
    "CandidateFilterReport",
    "HybridSearch",
    "HybridSearchResult",
    "HybridTraceContext",
    "ProcessedQuery",
    "QueryIntent",
    "QueryProcessor",
    "QueryRewriter",
    "RerankController",
    "RerankOutcome",
    "RerankTraceContext",
    "reciprocal_rank_fusion",
    "SparseRoute",
    "SparseTraceContext",
)
