"""Contain the RAG subsystem's pluggable component abstraction layer.

Each component package will keep its minimal abstract interface, registry-based
factory, and concrete implementations together. Application code depends on
these interfaces so LLMs, embeddings, splitters, transforms, vector stores,
rerankers, evaluators, and loaders can be selected through configuration
without changing orchestration code.

This layer defines integration boundaries only; it must not contain ingestion
or query business orchestration.
"""

__all__ = (
    "loader",
    "llm",
    "splitter",
    "transform",
    "embedding",
    "vector_store",
    "reranker",
    "evaluator",
)
