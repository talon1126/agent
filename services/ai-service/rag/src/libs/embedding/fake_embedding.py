"""Provide deterministic dense vectors for unit tests.

``FakeEmbedding`` avoids external API calls while preserving the exact
``embed()`` and ``embed_batch()`` contract expected from real embedding
providers. Vectors are derived from SHA256 bytes so the same input text always
produces the same output and different text usually produces different vectors.
"""

from __future__ import annotations

from hashlib import sha256

from src.core.errors import ProviderError
from src.libs.embedding.base_embedding import BaseEmbedding


class FakeEmbedding(BaseEmbedding):
    """Generate deterministic normalized vectors from text hashes."""

    def __init__(self, *, dimensions: int = 8) -> None:
        """Configure fake vector dimensionality.

        Args:
            dimensions: Positive number of float values returned per text.

        Raises:
            ProviderError: If ``dimensions`` is not positive.
        """

        if dimensions <= 0:
            raise ProviderError(
                "Fake embedding dimensions must be positive",
                context={"dimensions": dimensions},
            )
        self.dimensions = dimensions

    def embed(self, text: str) -> list[float]:
        """Return one deterministic dense vector for a text string.

        Args:
            text: Text to hash into vector values.

        Returns:
            A vector of length ``self.dimensions`` with values in ``[0, 1]``.

        Raises:
            ProviderError: If the text is blank.
        """

        if not text.strip():
            raise ProviderError("Cannot embed blank text")

        seed = sha256(text.encode()).digest()
        values: list[float] = []
        counter = 0
        while len(values) < self.dimensions:
            block = sha256(seed + counter.to_bytes(2, byteorder="big")).digest()
            values.extend(byte / 255.0 for byte in block)
            counter += 1
        return values[: self.dimensions]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed texts one by one while preserving order.

        Args:
            texts: Ordered text batch.

        Returns:
            Deterministic vectors in the same order as ``texts``.

        Raises:
            ProviderError: If any text is blank.
        """

        return [self.embed(text) for text in texts]
