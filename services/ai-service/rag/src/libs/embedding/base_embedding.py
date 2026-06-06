"""Define provider-independent dense embedding contracts.

Embedding clients convert text into dense vectors for ingestion and query
routes. This layer exposes only ``embed()`` and ``embed_batch()`` so callers do
not depend on OpenAI SDKs, model names, batching APIs, or response shapes.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class BaseEmbedding(ABC):
    """Provide the minimal unified embedding interface."""

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """Embed one text string into a dense vector.

        Args:
            text: Non-empty text that should be embedded.

        Returns:
            Dense vector values in provider-defined dimensions.

        Raises:
            ProviderError: Implementations raise this when the provider call or
                response validation fails.
        """

    @abstractmethod
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple text strings while preserving input order.

        Args:
            texts: Ordered non-empty texts that should be embedded.

        Returns:
            One dense vector per input text, in the same order.

        Raises:
            ProviderError: Implementations raise this when any provider call or
                response validation fails.
        """
