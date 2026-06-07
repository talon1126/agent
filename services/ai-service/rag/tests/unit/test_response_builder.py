"""Verify response-layer citation construction from retrieved chunk metadata.

D10 establishes the source-attribution boundary used by later response, MCP,
Dashboard, and AImodel adapters. These tests ensure citations remain grounded
in retrieval metadata, preserve ranked order, include the query trace ID, and
never guess a source from chunk prose.
"""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.core.response import Citation, CitationBuilder
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
