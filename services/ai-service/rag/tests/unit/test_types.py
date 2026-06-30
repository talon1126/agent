"""Protect shared domain-object and exception contracts for the RAG subsystem.

These tests define the stable data boundary used by ingestion, retrieval,
storage, response construction, and tracing. They focus on construction-time
validation and serialization so later modules can exchange objects without
repeating defensive checks or depending on implementation-specific classes.

Failures indicate a backwards-incompatible domain contract change or an
exception hierarchy that no longer supports layer-specific handling.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

RAG_ROOT = Path(__file__).resolve().parents[2]

# Repository-level pytest executes the independently installable RAG module
# directly from source. This path mirrors the package root exposed by an
# editable installation without importing unrelated ai-service packages.
sys.path.insert(0, str(RAG_ROOT))

types_module = importlib.import_module("src.core.types")
errors_module = importlib.import_module("src.core.errors")

Chunk = types_module.Chunk
Document = types_module.Document
ImageMetadata = types_module.ImageMetadata
RetrievalResult = types_module.RetrievalResult

ConfigurationError = errors_module.ConfigurationError
DatabaseError = errors_module.DatabaseError
IngestionError = errors_module.IngestionError
McpError = errors_module.McpError
ProviderError = errors_module.ProviderError
RagError = errors_module.RagError
RetrievalError = errors_module.RetrievalError


def build_image_metadata(**overrides: object) -> dict[str, object]:
    """Build one valid image fixture with optional field replacements.

    Args:
        **overrides: Image metadata fields that should replace defaults.

    Returns:
        A plain mapping suitable for ``Document.metadata["images"]``.
    """

    image: dict[str, object] = {
        "id": "image-1",
        "path": "data/images/shopping_guides/image-1.png",
    }
    image.update(overrides)
    return image


def test_image_metadata_preserves_public_image_identity_only() -> None:
    """Verify persisted image metadata exposes only stable public fields.

    Loader implementations may use page geometry and source offsets while
    inserting placeholders, but those temporary positioning details must not
    leak into the final ``Document.metadata.images`` contract.
    """
    image = ImageMetadata.model_validate(build_image_metadata())

    assert image.id == "image-1"
    assert image.path.endswith("image-1.png")
    assert image.model_dump() == {
        "id": "image-1",
        "path": "data/images/shopping_guides/image-1.png",
    }


def test_image_metadata_rejects_empty_identifiers_and_extra_position_fields() -> None:
    """Verify invalid or over-wide image records fail at construction time."""
    with pytest.raises(ValidationError):
        ImageMetadata.model_validate(build_image_metadata(id=""))

    with pytest.raises(ValidationError):
        ImageMetadata.model_validate(build_image_metadata(page=1))


def test_document_validates_and_normalizes_image_metadata() -> None:
    """Verify documents retain a dictionary metadata contract with typed images.

    Loaders may attach provider-specific metadata, so the outer mapping remains
    extensible. The reserved ``images`` list is validated against
    ``ImageMetadata`` and converted back to ordinary dictionaries for storage,
    JSON serialization, and metadata inheritance by ``DocumentChunker``.
    """
    document = Document(
        id="doc-1",
        text="[[image:image-1]] document body",
        metadata={
            "collection": "shopping_guides",
            "title": "Headphones Guide",
            "images": [build_image_metadata()],
        },
    )

    assert document.metadata["collection"] == "shopping_guides"
    assert document.metadata["images"][0]["id"] == "image-1"
    assert isinstance(document.metadata["images"][0], dict)


def test_document_summary_is_a_top_level_optional_field() -> None:
    """Verify document summaries stay outside extensible source metadata.

    Chunk rewrite, MCP summary tools, and Dashboard views need a document-level
    semantic summary without overloading ``metadata``. Blank summaries normalize
    to ``None`` so disabled or degraded summarization has one stable shape.
    """

    document = Document(
        id="doc-1",
        text="A guide about quiet office stress toys.",
        summary="Quiet office stress toy buying guidance.",
        metadata={"summary": "legacy metadata value"},
    )
    without_summary = Document(
        id="doc-2",
        text="A guide about wireless headphones.",
        summary="  ",
        metadata={},
    )

    assert document.summary == "Quiet office stress toy buying guidance."
    assert document.metadata["summary"] == "legacy metadata value"
    assert without_summary.summary is None


def test_document_rejects_blank_canonical_text() -> None:
    """Verify loaders cannot emit a document with no processable content.

    Empty or whitespace-only canonical text cannot be split, indexed, or traced
    meaningfully. Image-only documents still contain generated placeholders, so
    rejecting blank text does not block the approved multimodal ingestion path.
    """
    with pytest.raises(ValidationError, match="text"):
        Document(id="doc-1", text=" \n ", metadata={})


def test_chunk_enforces_offsets_and_metadata_source_fields() -> None:
    """Verify chunks expose source ranges and metadata-owned citations.

    ``start_offset`` is inclusive and ``end_offset`` is exclusive. Source path,
    document identity, collection, and section path live in ``metadata`` so the
    database stores one source contract owned by metadata.
    """
    chunk = Chunk(
        id="chunk-1",
        text="wireless headphones",
        metadata={
            "collection": "shopping_guides",
            "document_id": "doc-1",
            "source_path": "guides/headphones.md",
            "section_path": ["Audio", "Wireless"],
            "chunk_index": 0,
        },
        chunk_index=0,
        start_offset=20,
        end_offset=39,
    )

    assert chunk.chunk_index == 0
    assert chunk.start_offset == 20
    assert chunk.end_offset == 39
    assert chunk.metadata["document_id"] == "doc-1"
    assert chunk.metadata["source_path"] == "guides/headphones.md"

    with pytest.raises(ValidationError, match="unexpected_source"):
        Chunk(
            id="chunk-legacy-source-ref",
            text="legacy chunk",
            metadata={"document_id": "doc-1"},
            chunk_index=0,
            start_offset=0,
            end_offset=12,
            unexpected_source={"document_id": "doc-1"},
        )


def test_chunk_rejects_inverted_or_negative_ranges() -> None:
    """Verify malformed chunk ranges fail at the shared data boundary.

    Negative positions and an end before the start make source highlighting,
    image assignment, and citation reconstruction impossible. Both cases must
    fail before storage or retrieval receives the chunk.
    """
    common = {
        "id": "chunk-1",
        "text": "content",
        "metadata": {},
        "chunk_index": 0,
    }

    with pytest.raises(ValidationError):
        Chunk(**common, start_offset=-1, end_offset=7)

    with pytest.raises(ValidationError, match="end_offset"):
        Chunk(**common, start_offset=8, end_offset=7)

    with pytest.raises(ValidationError, match="end_offset"):
        Chunk(**common, start_offset=8, end_offset=8)


def test_searchable_types_reject_blank_text() -> None:
    """Verify empty searchable payloads cannot enter indexing or retrieval.

    A whitespace-only chunk wastes embedding and BM25 work, while a retrieval
    result without content cannot support an answer or citation. Both contracts
    must reject blank text before storage or response assembly.
    """
    with pytest.raises(ValidationError, match="text"):
        Chunk(
            id="chunk-1",
            text="   ",
            metadata={},
            chunk_index=0,
            start_offset=0,
            end_offset=3,
        )

    with pytest.raises(ValidationError, match="text"):
        RetrievalResult(
            chunk_id="chunk-1",
            text="\n",
            score=0.5,
            metadata={},
        )


def test_retrieval_result_preserves_route_score_without_normalizing_it() -> None:
    """Verify retrieval results retain route-native scores and chunk metadata.

    Dense and BM25 scores use different scales, so this type stores a finite
    float without forcing a zero-to-one range. Fusion logic may rank results but
    must not reinterpret the original score at this shared contract boundary.
    """
    result = RetrievalResult(
        chunk_id="chunk-1",
        text="retrieved content",
        score=12.75,
        metadata={"collection": "shopping_guides", "route": "sparse"},
    )

    assert result.score == 12.75
    assert result.metadata["route"] == "sparse"


@pytest.mark.parametrize(
    "error_type",
    [
        ConfigurationError,
        ProviderError,
        DatabaseError,
        IngestionError,
        RetrievalError,
        McpError,
    ],
)
def test_domain_errors_share_context_and_cause_contract(
    error_type: type[RagError],
) -> None:
    """Verify every layer-specific error can be handled as ``RagError``.

    The hierarchy preserves a readable message, structured context for trace
    output, and an optional original cause for exception chaining. Callers may
    catch one category for fallback or the base type at service boundaries.

    Args:
        error_type: Concrete error category supplied by pytest parametrization.
    """
    cause = RuntimeError("provider timeout")
    error = error_type(
        "operation failed",
        context={"provider": "fake", "stage": "test"},
        cause=cause,
    )

    assert isinstance(error, RagError)
    assert str(error) == "operation failed"
    assert error.context["stage"] == "test"
    assert error.cause is cause
    assert error.__cause__ is cause
