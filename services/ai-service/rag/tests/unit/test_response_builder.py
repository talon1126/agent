"""Verify citation and public knowledge-response construction.

D10 establishes the source-attribution boundary, while D11 adds the public
response contract consumed by MCP, Dashboard, CLI, and AImodel adapters. These
tests ensure citations remain grounded, image references are resolved in
retrieval order, response text is readable, and internal retrieval metadata is
never serialized as tool output.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import pytest

from src.core.response import (
    Citation,
    CitationBuilder,
    KnowledgeHubResponseBuilder,
    MultimodalAssembler,
)
from src.core.types import RetrievalResult


def _result(
    chunk_id: str,
    *,
    score: float = 0.8,
    metadata: dict[str, object] | None = None,
) -> RetrievalResult:
    """Build one retrieval result with citation-focused metadata."""

    return RetrievalResult(
        chunk_id=chunk_id,
        text=f"Retrieved content for {chunk_id}.",
        score=score,
        metadata=dict(metadata or {}),
    )


def test_citation_builder_uses_source_ref_and_preserves_ranked_order() -> None:
    """Require citations to follow reranked candidates and source_ref fields."""

    candidates = [
        _result(
            "chunk-b",
            score=0.91,
            metadata={
                "title": "Top-level title must not override source_ref",
                "source_ref": {
                    "document_id": "doc-headphones",
                    "source_path": "shopping_guides/headphones.md",
                    "title": "Wireless Headphones Buying Guide",
                    "section_path": ["Core Criteria", "Battery Life"],
                },
            },
        ),
        _result(
            "chunk-a",
            score=0.76,
            metadata={
                "source_ref": {
                    "document_id": "doc-fidget",
                    "source_uri": "shopping_guides/fidget-toys.pdf",
                    "title": "Fidget Toy Guide",
                    "heading_path": ["Quiet Office Options"],
                },
            },
        ),
    ]

    citations = CitationBuilder().build(
        candidates,
        trace_id="query-trace-001",
    )

    assert [citation.chunk_id for citation in citations] == ["chunk-b", "chunk-a"]
    assert citations[0] == Citation(
        document_id="doc-headphones",
        chunk_id="chunk-b",
        title="Wireless Headphones Buying Guide",
        section_path=("Core Criteria", "Battery Life"),
        source_uri="shopping_guides/headphones.md",
        score=0.91,
        trace_id="query-trace-001",
    )
    assert citations[1].section_path == ("Quiet Office Options",)
    assert citations[1].source_uri == "shopping_guides/fidget-toys.pdf"


def test_citation_builder_supports_top_level_metadata_and_source_title_fallback() -> None:
    """Require compatibility with dense results that expose no source_ref."""

    candidates = [
        _result(
            "chunk-a",
            metadata={
                "document_id": "doc-audio",
                "source_path": "shopping_guides/wireless-earbuds.md",
                "section_path": "Codec Selection",
            },
        )
    ]

    citations = CitationBuilder().build(candidates, trace_id="query-trace-002")

    assert citations[0].document_id == "doc-audio"
    assert citations[0].title == "wireless-earbuds"
    assert citations[0].section_path == ("Codec Selection",)
    assert citations[0].source_uri == "shopping_guides/wireless-earbuds.md"


def test_citation_builder_does_not_mutate_retrieval_metadata() -> None:
    """Require citation normalization to leave shared retrieval input untouched."""

    candidate = _result(
        "chunk-a",
        metadata={
            "source_ref": {
                "document_id": "doc-a",
                "source_path": "shopping_guides/a.md",
                "section_path": ["A", "B"],
            }
        },
    )
    original_metadata = deepcopy(candidate.metadata)

    citations = CitationBuilder().build([candidate], trace_id="query-trace-003")

    assert citations[0].section_path == ("A", "B")
    assert candidate.metadata == original_metadata


def test_citation_builder_accepts_null_source_ref_with_top_level_fallback() -> None:
    """Treat a persisted null source_ref as absent compatibility metadata."""

    citation = CitationBuilder().build(
        [
            _result(
                "chunk-a",
                metadata={
                    "source_ref": None,
                    "document_id": "doc-a",
                    "source_path": "shopping_guides/a.md",
                },
            )
        ],
        trace_id="query-trace-null-source",
    )[0]

    assert citation.document_id == "doc-a"
    assert citation.source_uri == "shopping_guides/a.md"


def test_citation_builder_rejects_non_mapping_source_ref() -> None:
    """Reject malformed nested source metadata before constructing citations."""

    with pytest.raises(ValueError, match="source_ref must be a mapping"):
        CitationBuilder().build(
            [
                _result(
                    "chunk-a",
                    metadata={
                        "source_ref": ["invalid"],
                        "document_id": "doc-a",
                        "source_path": "shopping_guides/a.md",
                    },
                )
            ],
            trace_id="query-trace-invalid-source",
        )


@pytest.mark.parametrize(
    "metadata,expected_message",
    [
        (
            {"source_ref": {"source_path": "shopping_guides/a.md"}},
            "document_id",
        ),
        (
            {"source_ref": {"document_id": "doc-a"}},
            "source path",
        ),
        (
            {
                "source_ref": {
                    "document_id": "doc-a",
                    "source_path": "shopping_guides/a.md",
                    "section_path": {"invalid": "mapping"},
                }
            },
            "section path",
        ),
        (
            {
                "source_ref": {
                    "document_id": "doc-a",
                    "source_path": {"invalid": "path"},
                }
            },
            "source path",
        ),
        (
            {
                "source_ref": {
                    "document_id": "doc-a",
                    "source_path": "shopping_guides/a.md",
                    "title": ["invalid", "title"],
                }
            },
            "title",
        ),
    ],
)
def test_citation_builder_rejects_unverifiable_source_metadata(
    metadata: dict[str, object],
    expected_message: str,
) -> None:
    """Require incomplete or malformed source metadata to fail explicitly."""

    with pytest.raises(ValueError, match=expected_message):
        CitationBuilder().build(
            [_result("chunk-a", metadata=metadata)],
            trace_id="query-trace-004",
        )


def test_citation_builder_validates_trace_id_before_processing_candidates() -> None:
    """Require every citation to be linked to a non-blank query trace."""

    candidate = _result(
        "chunk-a",
        metadata={
            "document_id": "doc-a",
            "source_path": "shopping_guides/a.md",
        },
    )

    with pytest.raises(ValueError, match="trace_id"):
        CitationBuilder().build([candidate], trace_id=" ")


def test_citation_builder_returns_empty_list_for_empty_results() -> None:
    """Require an empty retrieval result set to produce no invented citations."""

    assert CitationBuilder().build([], trace_id="query-trace-empty") == []


def test_citation_serializes_section_path_as_json_array() -> None:
    """Require the shared Citation contract to be directly JSON-compatible."""

    citation = CitationBuilder().build(
        [
            _result(
                "chunk-a",
                metadata={
                    "document_id": "doc-a",
                    "source_uri": "https://example.com/guides/audio%20guide.md?version=2",
                    "section_path": ["Audio", "Wireless"],
                },
            )
        ],
        trace_id="query-trace-json",
    )[0]

    payload = citation.model_dump(mode="json")

    assert payload["section_path"] == ["Audio", "Wireless"]
    assert payload["title"] == "audio guide"


@dataclass(frozen=True)
class _ImageRecord:
    """Provide the image-index fields consumed by ``MultimodalAssembler``."""

    image_id: str
    file_path: str
    mime_type: str | None
    page_num: int | None
    width: int | None
    height: int | None
    quality_status: str
    metadata: dict[str, Any]


class _ImageResolver:
    """Record batch lookups while returning image records in storage order."""

    def __init__(self, records: list[_ImageRecord]) -> None:
        """Store deterministic records for response-layer unit tests.

        Args:
            records: Image index records returned by ``find_by_ids``.
        """

        self.records = records
        self.requests: list[tuple[str, ...]] = []

    def find_by_ids(self, image_ids: list[str]) -> list[_ImageRecord]:
        """Return configured records and capture the requested stable IDs.

        Args:
            image_ids: Ordered unique image IDs collected from ranked chunks.

        Returns:
            Configured records. Their order intentionally differs from request
            order in tests so the assembler must restore reference order.
        """

        self.requests.append(tuple(image_ids))
        return list(self.records)


def test_response_builder_formats_context_and_assembles_ranked_images() -> None:
    """Build a readable public response without exposing retrieval internals."""

    candidates = [
        RetrievalResult(
            chunk_id="chunk-headphones",
            text="  Battery life and codec support should be compared together.  ",
            score=0.94,
            metadata={
                "source_ref": {
                    "document_id": "doc-headphones",
                    "source_path": "shopping_guides/headphones.md",
                    "title": "Wireless Headphones Guide",
                },
                "image_refs": ["image-codec", "image-battery"],
                "tool_result": {
                    "tool": "dense_search",
                    "raw_vector": [0.1, 0.2],
                },
            },
        ),
        RetrievalResult(
            chunk_id="chunk-battery",
            text="A charging-case rating should be separated from earbud runtime.",
            score=0.88,
            metadata={
                "source_ref": {
                    "document_id": "doc-headphones",
                    "source_path": "shopping_guides/headphones.md",
                    "title": "Wireless Headphones Guide",
                },
                "image_refs": ["image-battery"],
                "dense_score": 0.995,
            },
        ),
    ]
    resolver = _ImageResolver(
        [
            _ImageRecord(
                image_id="image-battery",
                file_path="data/images/shopping_guides/image-battery.png",
                mime_type="image/png",
                page_num=3,
                width=640,
                height=360,
                quality_status="ok",
                metadata={"caption": "Charging case and battery-life table."},
            ),
            _ImageRecord(
                image_id="image-codec",
                file_path="data/images/shopping_guides/image-codec.jpg",
                mime_type="image/jpeg",
                page_num=2,
                width=800,
                height=600,
                quality_status="low_quality",
                metadata={"caption": ""},
            ),
        ]
    )
    builder = KnowledgeHubResponseBuilder(
        multimodal_assembler=MultimodalAssembler(resolver=resolver)
    )

    response = builder.build(candidates, trace_id="query-trace-response")

    assert response.ok is True
    assert response.is_empty is False
    assert response.content == (
        "[1] Battery life and codec support should be compared together.\n\n"
        "[2] A charging-case rating should be separated from earbud runtime."
    )
    assert [citation.chunk_id for citation in response.citations] == [
        "chunk-headphones",
        "chunk-battery",
    ]
    assert resolver.requests == [("image-codec", "image-battery")]
    assert [image.image_id for image in response.images] == [
        "image-codec",
        "image-battery",
    ]
    assert response.images[0].chunk_ids == ("chunk-headphones",)
    assert response.images[0].caption is None
    assert response.images[1].chunk_ids == (
        "chunk-headphones",
        "chunk-battery",
    )
    assert response.images[1].caption == (
        "Charging case and battery-life table."
    )

    payload = response.model_dump(mode="json")
    serialized = str(payload)
    assert set(payload) == {
        "ok",
        "content",
        "citations",
        "images",
        "trace_id",
        "is_empty",
    }
    assert "tool_result" not in serialized
    assert "dense_score" not in serialized
    assert "raw_vector" not in serialized


def test_multimodal_assembler_skips_missing_records_without_mutating_results() -> None:
    """Degrade to available images while preserving retrieval metadata."""

    candidate = _result(
        "chunk-a",
        metadata={
            "document_id": "doc-a",
            "source_path": "shopping_guides/a.md",
            "image_refs": ["image-missing", "image-found", "image-found"],
        },
    )
    original_metadata = deepcopy(candidate.metadata)
    resolver = _ImageResolver(
        [
            _ImageRecord(
                image_id="image-found",
                file_path="data/images/shopping_guides/image-found.png",
                mime_type="image/png",
                page_num=None,
                width=None,
                height=None,
                quality_status="skipped",
                metadata={},
            )
        ]
    )

    images = MultimodalAssembler(resolver=resolver).assemble([candidate])

    assert [image.image_id for image in images] == ["image-found"]
    assert resolver.requests == [("image-missing", "image-found")]
    assert candidate.metadata == original_metadata


def test_multimodal_assembler_rejects_duplicate_resolver_records() -> None:
    """Reject ambiguous storage responses containing one image ID twice."""

    candidate = _result(
        "chunk-a",
        metadata={
            "document_id": "doc-a",
            "source_path": "shopping_guides/a.md",
            "image_refs": ["image-a"],
        },
    )
    duplicate = _ImageRecord(
        image_id="image-a",
        file_path="data/images/shopping_guides/image-a.png",
        mime_type="image/png",
        page_num=1,
        width=100,
        height=100,
        quality_status="ok",
        metadata={},
    )

    with pytest.raises(ValueError, match="duplicate record"):
        MultimodalAssembler(
            resolver=_ImageResolver([duplicate, duplicate])
        ).assemble([candidate])


def test_multimodal_assembler_rejects_invalid_image_reference_contracts() -> None:
    """Reject malformed image_refs instead of stringifying internal objects."""

    candidate = _result(
        "chunk-a",
        metadata={
            "document_id": "doc-a",
            "source_path": "shopping_guides/a.md",
            "image_refs": [{"id": "image-a"}],
        },
    )

    with pytest.raises(ValueError, match="image_refs"):
        MultimodalAssembler(resolver=_ImageResolver([])).assemble([candidate])


def test_response_builder_returns_explicit_empty_response_without_image_lookup() -> None:
    """Represent no retrieval hits without inventing context or citations."""

    resolver = _ImageResolver([])
    response = KnowledgeHubResponseBuilder(
        multimodal_assembler=MultimodalAssembler(resolver=resolver)
    ).build([], trace_id="query-trace-empty-response")

    assert response.ok is True
    assert response.is_empty is True
    assert response.content == ""
    assert response.citations == ()
    assert response.images == ()
    assert resolver.requests == []


def test_response_builder_requires_a_non_blank_trace_id() -> None:
    """Reject responses that cannot be correlated with a query trace."""

    with pytest.raises(ValueError, match="trace_id"):
        KnowledgeHubResponseBuilder().build([], trace_id=" ")
