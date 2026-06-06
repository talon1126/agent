"""Define the shared exception hierarchy for RAG subsystem boundaries.

Every subsystem-specific error derives from ``RagError`` so service and MCP
boundaries can handle failures consistently, while narrower categories allow
pipelines to apply targeted fallback behavior. Errors preserve structured
context for trace output and an optional original cause for diagnostics.

This module does not decide HTTP status codes, MCP error content, logging, or
fallback policy. Those responsibilities belong to adapters and orchestration
layers that catch these exceptions.
"""

from __future__ import annotations

from typing import Any


class RagError(Exception):
    """Base exception carrying a readable message and trace-safe context.

    Args:
        message: User- or operator-readable failure description.
        context: Optional structured details such as provider, stage, trace ID,
            collection, or document ID. Secrets must never be included.
        cause: Optional original exception retained for diagnostics and explicit
            exception chaining by callers.

    Attributes:
        context: Shallow-copied structured diagnostic metadata.
        cause: Original exception, when one exists.
    """

    def __init__(
        self,
        message: str,
        *,
        context: dict[str, Any] | None = None,
        cause: Exception | None = None,
    ) -> None:
        """Initialize a categorized RAG failure without formatting internals.

        Args:
            message: Stable readable error message returned by ``str(error)``.
            context: Optional trace-safe diagnostic mapping.
            cause: Optional lower-level exception that triggered this failure.
        """

        super().__init__(message)
        self.context = dict(context or {})
        self.cause = cause
        self.__cause__ = cause


class ConfigurationError(RagError):
    """Report invalid settings, Prompt definitions, or environment references."""


class ProviderError(RagError):
    """Report failures raised by LLM, embedding, reranker, or loader providers."""


class DatabaseError(RagError):
    """Report PostgreSQL, pgvector, repository, or transaction failures."""


class IngestionError(RagError):
    """Report document loading, chunking, transform, or indexing failures."""


class RetrievalError(RagError):
    """Report query processing, route, fusion, filtering, or reranking failures."""


class McpError(RagError):
    """Report invalid MCP tool input or transport-facing tool failures."""
