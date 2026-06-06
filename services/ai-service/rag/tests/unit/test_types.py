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
        "page": 2,
        "text_offset": 10,
        "text_length": 14,
        "position": {"x": 12, "y": 30, "width": 640, "height": 480},
    }
    image.update(overrides)
    return image


def test_image_metadata_preserves_source_location() -> None:
    """Verify image metadata carries logical and physical source positions.

    The loader and splitter rely on text offsets to associate placeholders with
    chunks, while the dashboard needs page and physical position information.
    The serialized model must remain a plain JSON-compatible mapping.
    """
    image = ImageMetadata.model_validate(build_image_metadata())

    assert image.id == "image-1"
    assert image.page == 2
    assert image.text_offset == 10
    assert image.text_length == 14
    assert image.position["width"] == 640
    assert image.model_dump()["path"].endswith("image-1.png")


def test_image_metadata_rejects_negative_offsets_and_empty_identifiers() -> None:
    """Verify invalid image references fail before chunk distribution.

    Negative offsets cannot intersect document/chunk ranges, and blank IDs or
    paths cannot be persisted or returned as references. Construction-time
    validation prevents these malformed values from reaching ingestion logic.
    """
    with pytest.raises(ValidationError):
        ImageMetadata.model_validate(build_image_metadata(id=""))

    with pytest.raises(ValidationError):
        ImageMetadata.model_validate(build_image_metadata(text_offset=-1))


def test_image_metadata_requires_physical_position() -> None:
    """Verify every extracted image includes its physical source position.

    The A6 contract defines ``position`` as required metadata used by document
    inspection and multimodal response assembly. Silently replacing an omitted
    value with an empty mapping would hide incomplete loader output and make the
    source image impossible to locate reliably.
    """
    image = build_image_metadata()
    image.pop("position")

    with pytest.raises(ValidationError, match="position"):
        ImageMetadata.model_validate(image)

    with pytest.raises(ValidationError, match="position"):
        ImageMetadata.model_validate(build_image_metadata(position={}))


def test_document_validates_and_normalizes_image_metadata() -> None:
    """Verify documents retain a dictionary metadata contract with typed images.

    Loaders may attach provider-specific metadata, so the outer mapping remains
    extensible. The reserved ``images`` list is validated against
    ``ImageMetadata`` and converted back to ordinary dictionaries for storage,
    JSON serialization, and metadata inheritance by ``DocumentChunker``.
    """
    document = Document(
        id="doc-1",
        text="0123456789[image:one] document body",
        metadata={
            "collection": "shopping_guides",
            "title": "Headphones Guide",
            "images": [build_image_metadata()],
        },
    )

    assert document.metadata["collection"] == "shopping_guides"
    assert document.metadata["images"][0]["id"] == "image-1"
    assert isinstance(document.metadata["images"][0], dict)


def test_document_rejects_image_placeholder_outside_text() -> None:
    """Verify an image placeholder range cannot exceed document text bounds.

    Splitter image-reference distribution assumes every ``text_offset`` and
    ``text_length`` range points into the canonical document text. Rejecting an
    impossible range here avoids silently dropping image references later.
    """
    with pytest.raises(ValidationError, match="exceeds document text length"):
        Document(
            id="doc-1",
            text="short",
            metadata={"images": [build_image_metadata(text_offset=4, text_length=10)]},
        )


def test_document_rejects_blank_canonical_text() -> None:
    """Verify loaders cannot emit a document with no processable content.

    Empty or whitespace-only canonical text cannot be split, indexed, or traced
    meaningfully. Image-only documents still contain generated placeholders, so
    rejecting blank text does not block the approved multimodal ingestion path.
    """
    with pytest.raises(ValidationError, match="text"):
        Document(id="doc-1", text=" \n ", metadata={})


def test_chunk_enforces_offsets_and_optional_source_reference() -> None:
    """Verify chunks expose stable source ranges and optional citation context.

    ``start_offset`` is inclusive and ``end_offset`` is exclusive. The source
    reference remains an extensible mapping because its exact citation fields
    depend on loader output, while omission is valid for synthetic content.
    """
    chunk = Chunk(
        id="chunk-1",
        text="wireless headphones",
        metadata={"collection": "shopping_guides", "chunk_index": 0},
        chunk_index=0,
        start_offset=20,
        end_offset=39,
        source_ref={
            "document_id": "doc-1",
            "source_path": "guides/headphones.md",
            "section_path": ["Audio", "Wireless"],
            "collection": "shopping_guides",
        },
    )

    assert chunk.chunk_index == 0
    assert chunk.start_offset == 20
    assert chunk.end_offset == 39
    assert chunk.source_ref["document_id"] == "doc-1"

    synthetic_chunk = chunk.model_copy(update={"id": "chunk-2", "source_ref": None})
    assert synthetic_chunk.source_ref is None


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
        "source_ref": None,
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
            source_ref=None,
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
