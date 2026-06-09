"""Protect text-splitting boundaries and Document-to-Chunk adaptation.

``libs.splitter`` is intentionally a pure text utility layer. The business
conversion from ``Document`` to ``Chunk`` belongs to ``DocumentChunker`` so
metadata inheritance, image-reference distribution, source offsets, and stable
IDs stay in one testable ingestion adapter.
"""

from __future__ import annotations

import importlib
import sys
from copy import deepcopy
from pathlib import Path

import pytest

RAG_ROOT = Path(__file__).resolve().parents[2]

# Repository-level pytest runs this independently installable RAG module
# directly from source. Add only the RAG root to match editable-install imports.
sys.path.insert(0, str(RAG_ROOT))

types_module = importlib.import_module("src.core.types")
errors_module = importlib.import_module("src.core.errors")
chunk_id_module = importlib.import_module("src.ingestion.chunk.chunk_id")
chunker_module = importlib.import_module("src.ingestion.chunk.document_chunker")
splitter_step_module = importlib.import_module("src.ingestion.chunk.splitter_step")
splitter_module = importlib.import_module("src.libs.splitter")

Chunk = types_module.Chunk
Document = types_module.Document
IngestionError = errors_module.IngestionError
build_chunk_id = chunk_id_module.build_chunk_id
DocumentChunker = chunker_module.DocumentChunker
SplitterStep = splitter_step_module.SplitterStep
FakeSplitter = splitter_module.FakeSplitter


def test_splitter_layer_returns_plain_text_segments_only() -> None:
    """Require splitter implementations to stay free of business objects.

    Later splitters may use LangChain internally, but their public contract must
    remain ``str -> list[str]``. ``DocumentChunker`` owns conversion to
    ``Chunk`` objects.
    """

    splitter = FakeSplitter(chunks=["first segment", "second segment"])

    parts = splitter.split("ignored source text")

    assert parts == ["first segment", "second segment"]
    assert all(isinstance(part, str) for part in parts)
    assert not any(isinstance(part, Chunk) for part in parts)


def test_document_chunker_converts_document_to_business_chunks() -> None:
    """Require DocumentChunker to add every business field around text splits.

    The adapter must preserve inherited metadata, add ordered chunk indexes,
    compute source offsets, build source references, distribute image IDs based
    on placeholder ranges, and return validated ``Chunk`` objects.
    """

    document_text = (
        "Alpha intro [image:one] details.\n"
        "Beta comparison details.\n"
        "Gamma outro [image:two] details."
    )
    first_image_offset = document_text.index("[image:one]")
    second_image_offset = document_text.index("[image:two]")
    first_image = {
        "id": "image-1",
        "path": "data/images/shopping_guides/image-1.png",
        "page": 1,
        "text_offset": first_image_offset,
        "text_length": len("[image:one]"),
        "position": {"x": 10, "y": 20, "width": 300, "height": 200},
    }
    second_image = {
        "id": "image-2",
        "path": "data/images/shopping_guides/image-2.png",
        "page": 2,
        "text_offset": second_image_offset,
        "text_length": len("[image:two]"),
        "position": {"x": 15, "y": 25, "width": 320, "height": 220},
    }
    document = Document(
        id="doc-shopping-guide",
        text=document_text,
        metadata={
            "source_path": "shopping_guides/headphones.md",
            "heading_path": ["Guides", "Headphones"],
            "doc_type": "shopping_guide",
            "images": [first_image, second_image],
        },
    )
    chunker = DocumentChunker(
        splitter=FakeSplitter(
            chunks=[
                "Alpha intro [image:one] details.",
                "Beta comparison details.",
                "Gamma outro [image:two] details.",
            ]
        )
    )

    chunks = chunker.chunk(document)
    repeated_chunks = chunker.chunk(document)

    assert [chunk.chunk_index for chunk in chunks] == [0, 1, 2]
    assert all(isinstance(chunk, Chunk) for chunk in chunks)
    assert [chunk.id for chunk in chunks] == [chunk.id for chunk in repeated_chunks]
    assert document.metadata["images"] == [first_image, second_image]
    assert chunks[0].metadata["doc_type"] == "shopping_guide"
    assert chunks[0].metadata["chunk_index"] == 0
    assert chunks[1].metadata["chunk_index"] == 1
    assert chunks[2].metadata["chunk_index"] == 2
    assert chunks[0].metadata["section_path"] == ["Guides", "Headphones"]
    assert chunks[0].metadata["image_refs"] == ["image-1"]
    assert [image["id"] for image in chunks[0].metadata["images"]] == ["image-1"]
    assert "image_refs" not in chunks[1].metadata
    assert "images" not in chunks[1].metadata
    assert chunks[2].metadata["image_refs"] == ["image-2"]
    assert [image["id"] for image in chunks[2].metadata["images"]] == ["image-2"]
    assert chunks[0].source_ref == {
        "document_id": "doc-shopping-guide",
        "source_path": "shopping_guides/headphones.md",
        "section_path": ["Guides", "Headphones"],
        "start_offset": 0,
        "end_offset": len("Alpha intro [image:one] details."),
    }
    assert chunks[1].start_offset == document_text.index("Beta comparison details.")
    assert chunks[2].start_offset == document_text.index("Gamma outro")
    assert chunks[2].end_offset == len(document_text)
    assert document.metadata.get("image_refs") is None


def test_build_chunk_id_changes_for_each_identity_component() -> None:
    """Protect the source, section, and content inputs of stable chunk IDs.

    Re-ingesting the same source section and content must reproduce the same
    identifier. Any identity component change must generate a different ID so
    storage upserts cannot overwrite an unrelated source, section, or version.
    """

    original = build_chunk_id(
        source_path="shopping_guides/headphones.md",
        section_path=["Guides", "Wireless"],
        text="Choose a low-latency codec for gaming.",
    )

    assert original == build_chunk_id(
        source_path="shopping_guides/headphones.md",
        section_path=["Guides", "Wireless"],
        text="Choose a low-latency codec for gaming.",
    )
    assert original != build_chunk_id(
        source_path="shopping_guides/earbuds.md",
        section_path=["Guides", "Wireless"],
        text="Choose a low-latency codec for gaming.",
    )
    assert original != build_chunk_id(
        source_path="shopping_guides/headphones.md",
        section_path=["Guides", "Wired"],
        text="Choose a low-latency codec for gaming.",
    )
    assert original != build_chunk_id(
        source_path="shopping_guides/headphones.md",
        section_path=["Guides", "Wireless"],
        text="Choose an active-noise-cancelling model for commuting.",
    )


def test_document_chunker_attaches_active_heading_path_to_each_chunk() -> None:
    """Require section metadata and IDs to follow the active Markdown heading.

    Loader metadata contains the ordered heading hierarchy for the complete
    document. DocumentChunker must select the last heading preceding each
    source range instead of copying one document-wide path to every chunk.
    """

    document_text = (
        "# Headphones\n"
        "General buying advice.\n"
        "## Gaming\n"
        "Prefer low latency.\n"
        "## Commuting\n"
        "Prefer strong noise cancellation."
    )
    document = Document(
        id="doc-heading-paths",
        text=document_text,
        metadata={
            "source_path": "shopping_guides/headphones.md",
            "headings": [
                {
                    "level": 1,
                    "title": "Headphones",
                    "path": ["Headphones"],
                    "text_offset": document_text.index("# Headphones"),
                },
                {
                    "level": 2,
                    "title": "Gaming",
                    "path": ["Headphones", "Gaming"],
                    "text_offset": document_text.index("## Gaming"),
                },
                {
                    "level": 2,
                    "title": "Commuting",
                    "path": ["Headphones", "Commuting"],
                    "text_offset": document_text.index("## Commuting"),
                },
            ],
        },
    )
    chunker = DocumentChunker(
        splitter=FakeSplitter(
            chunks=[
                "General buying advice.",
                "Prefer low latency.",
                "Prefer strong noise cancellation.",
            ]
        )
    )

    chunks = chunker.chunk(document)

    assert [chunk.metadata["section_path"] for chunk in chunks] == [
        ["Headphones"],
        ["Headphones", "Gaming"],
        ["Headphones", "Commuting"],
    ]
    assert len({chunk.id for chunk in chunks}) == 3
    assert chunks[1].source_ref["section_path"] == ["Headphones", "Gaming"]


def test_document_chunker_deep_copies_document_metadata() -> None:
    """Require each chunk to own independent nested metadata structures.

    Later transform stages mutate chunk metadata. Sharing nested dictionaries
    or lists with the source Document or sibling chunks would make one
    transform silently alter unrelated pipeline objects.
    """

    document = Document(
        id="doc-metadata-copy",
        text="First section.\nSecond section.",
        metadata={
            "source_path": "shopping_guides/copy.md",
            "classification": {"tags": ["audio"]},
        },
    )
    original_metadata = deepcopy(document.metadata)
    chunks = DocumentChunker(
        splitter=FakeSplitter(chunks=["First section.", "Second section."])
    ).chunk(document)

    chunks[0].metadata["classification"]["tags"].append("mutated")

    assert document.metadata == original_metadata
    assert chunks[1].metadata["classification"]["tags"] == ["audio"]


def test_document_chunker_rejects_segment_that_is_not_in_source() -> None:
    """Require invalid splitter output to fail before creating false offsets."""

    document = Document(
        id="doc-invalid-segment",
        text="Canonical source text.",
        metadata={"source_path": "shopping_guides/source.md"},
    )

    with pytest.raises(IngestionError, match="Unable to locate splitter segment"):
        DocumentChunker(
            splitter=FakeSplitter(chunks=["Text invented by a broken splitter."])
        ).chunk(document)


def test_splitter_step_delegates_document_adaptation() -> None:
    """Require the pipeline step to expose the Document-to-Chunk boundary.

    Pipeline code should depend on one ingestion step rather than invoking a
    low-level text splitter directly. This keeps business adaptation mandatory
    when the full ingestion pipeline is assembled later.
    """

    document = Document(
        id="doc-step",
        text="One complete section.",
        metadata={"source_path": "shopping_guides/step.md"},
    )
    step = SplitterStep(
        chunker=DocumentChunker(splitter=FakeSplitter(chunks=["One complete section."]))
    )

    chunks = step.run(document)

    assert len(chunks) == 1
    assert isinstance(chunks[0], Chunk)
    assert chunks[0].source_ref["document_id"] == "doc-step"


def test_document_chunker_locates_overlapping_splitter_segments() -> None:
    """Require chunk offset detection to support overlapping text splitters.

    ``settings.yaml`` configures chunk overlap for the recursive splitter. The
    adapter must therefore search for the next segment from the previous start,
    not only after the previous end, otherwise valid overlapping chunks cannot
    be converted to ``Chunk`` objects.
    """

    document = Document(
        id="doc-overlap",
        text="abcdefghij",
        metadata={"source_path": "shopping_guides/overlap.md"},
    )
    chunker = DocumentChunker(
        splitter=FakeSplitter(
            chunks=[
                "abcdef",
                "defghi",
                "hij",
            ]
        )
    )

    chunks = chunker.chunk(document)

    assert [(chunk.start_offset, chunk.end_offset) for chunk in chunks] == [
        (0, 6),
        (3, 9),
        (7, 10),
    ]
