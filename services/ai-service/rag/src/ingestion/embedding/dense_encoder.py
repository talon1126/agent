"""Generate dense vectors for chunks that need semantic indexing.

``DenseEncoder`` is the boundary between transformed chunks and the dense
embedding provider. It computes a SHA256 hash from the current chunk text,
decides whether storage already contains that exact content, embeds one chunk
for the C6 path, and embeds a bounded ordered batch for the C8 path through the
provider-independent ``BaseEmbedding`` contract. It deliberately does not retry
failures, write pgvector rows, or build sparse indexes.
"""

from __future__ import annotations

from hashlib import sha256
from math import isfinite
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core.errors import IngestionError
from src.core.types import Chunk
from src.libs.embedding import BaseEmbedding


class DenseEncodingResult(BaseModel):
    """Carry one chunk's dense vector and storage-facing content hash."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    chunk_id: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    vector: list[float] = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("vector")
    @classmethod
    def reject_non_numeric_vector(cls, value: list[float]) -> list[float]:
        """Normalize vector values to floats and reject invalid items.

        Args:
            value: Candidate dense vector returned by an embedding provider.

        Returns:
            A float-normalized vector.

        Raises:
            TypeError or ValueError: If one item cannot be converted to float.
        """

        vector = [float(item) for item in value]
        if not all(isfinite(item) for item in vector):
            raise ValueError("Dense vector values must be finite")
        return vector


class DenseEncoder:
    """Encode individual chunks with a dense embedding provider."""

    def __init__(self, *, embedding: BaseEmbedding) -> None:
        """Configure the dense embedding dependency.

        Args:
            embedding: Provider-independent embedding client. Unit tests usually
                pass ``FakeEmbedding`` or a mock; production code will use the
                factory-selected OpenAI embedding adapter.
        """

        self._embedding = embedding

    def content_hash(self, chunk: Chunk) -> str:
        """Compute the stable content hash used for differential encoding.

        Args:
            chunk: Chunk whose current transformed text should be indexed.

        Returns:
            Lowercase SHA256 digest of ``chunk.text`` encoded as UTF-8.
        """

        return _content_hash(chunk.text)

    def should_encode(
        self,
        chunk: Chunk,
        *,
        existing_content_hashes: set[str],
    ) -> bool:
        """Decide whether a chunk requires a new dense embedding.

        Args:
            chunk: Candidate chunk.
            existing_content_hashes: Hashes already present in storage for the
                current collection or ingestion scope.

        Returns:
            ``True`` when the current chunk text hash is absent from storage.
        """

        return self.content_hash(chunk) not in existing_content_hashes

    def encode(self, chunk: Chunk) -> DenseEncodingResult:
        """Embed one chunk and return a storage-facing result object.

        Args:
            chunk: Chunk whose text should be embedded.

        Returns:
            A ``DenseEncodingResult`` containing chunk ID, content hash, vector,
            and minimal trace/debug metadata.

        Raises:
            IngestionError: If the embedding provider fails or returns an
                invalid vector.
        """

        content_hash = self.content_hash(chunk)
        try:
            vector = self._embedding.embed(chunk.text)
            return DenseEncodingResult(
                chunk_id=chunk.id,
                content_hash=content_hash,
                vector=vector,
                metadata={"chunk_index": chunk.chunk_index},
            )
        except Exception as error:
            raise IngestionError(
                "Unable to encode dense vector",
                context={
                    "operation": "dense_encode",
                    "chunk_id": chunk.id,
                    "content_hash": content_hash,
                },
                cause=error,
            ) from error

    def encode_batch(self, chunks: list[Chunk]) -> list[DenseEncodingResult]:
        """Embed an ordered chunk batch with one provider batch request.

        Args:
            chunks: Non-empty ordered chunks whose text should be embedded.

        Returns:
            One ``DenseEncodingResult`` per chunk in the same input order.

        Raises:
            IngestionError: If the provider batch call fails, returns a vector
                count different from the chunk count, or any vector fails
                validation.
        """

        if not chunks:
            return []

        content_hashes = [self.content_hash(chunk) for chunk in chunks]
        try:
            vectors = self._embedding.embed_batch([chunk.text for chunk in chunks])
            if len(vectors) != len(chunks):
                raise ValueError(
                    "Embedding provider returned an unexpected vector count; "
                    f"chunks={len(chunks)}, vectors={len(vectors)}"
                )
            return [
                DenseEncodingResult(
                    chunk_id=chunk.id,
                    content_hash=content_hash,
                    vector=vector,
                    metadata={"chunk_index": chunk.chunk_index},
                )
                for chunk, content_hash, vector in zip(
                    chunks,
                    content_hashes,
                    vectors,
                    strict=True,
                )
            ]
        except Exception as error:
            raise IngestionError(
                "Unable to encode dense vector batch",
                context={
                    "operation": "dense_encode_batch",
                    "chunk_ids": [chunk.id for chunk in chunks],
                    "content_hashes": content_hashes,
                },
                cause=error,
            ) from error


def _content_hash(text: str) -> str:
    """Return the SHA256 digest shared by dense encoding and storage.

    Args:
        text: Exact transformed chunk text.

    Returns:
        Lowercase SHA256 hexadecimal digest.
    """

    return sha256(text.encode("utf-8")).hexdigest()
