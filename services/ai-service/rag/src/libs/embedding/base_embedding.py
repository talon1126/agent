"""Define provider-independent dense embedding contracts.

Embedding clients convert text into dense vectors for ingestion and query
routes. This layer exposes single-text and batch vector generation so callers do
not depend on OpenAI SDKs, model names, batching APIs, or response shapes.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod


class BaseEmbedding(ABC):
    """Provide the minimal unified embedding interface."""

    async def async_embed(self, text: str) -> list[float]:
        """Embed one text string without blocking the event-loop caller.

        Args:
            text: Non-empty text that should be embedded.

        Returns:
            Dense vector values in provider-defined dimensions.

        Raises:
            ProviderError: Implementations raise this when the provider call or
                response validation fails. The default compatibility path
                delegates to ``embed()`` in a worker thread until providers add
                native async transports.
        """

        return await asyncio.to_thread(self.embed, text)

    async def async_embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed multiple text strings without blocking the event-loop caller.

        Args:
            texts: Ordered non-empty texts that should be embedded.

        Returns:
            One dense vector per input text, in the same order.

        Raises:
            ProviderError: Implementations raise this when any provider call or
                response validation fails. The default compatibility path
                delegates to ``embed_batch()`` in a worker thread.
        """

        return await asyncio.to_thread(self.embed_batch, texts)

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
