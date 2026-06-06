"""Protect text-splitting boundaries and Document-to-Chunk adaptation.

``libs.splitter`` is intentionally a pure text utility layer. The business
conversion from ``Document`` to ``Chunk`` belongs to ``DocumentChunker`` so
metadata inheritance, image-reference distribution, source offsets, and stable
IDs stay in one testable ingestion adapter.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

RAG_ROOT = Path(__file__).resolve().parents[2]

# Repository-level pytest runs this independently installable RAG module
# directly from source. Add only the RAG root to match editable-install imports.
sys.path.insert(0, str(RAG_ROOT))

types_module = importlib.import_module("src.core.types")
chunker_module = importlib.import_module("src.ingestion.chunk.document_chunker")
splitter_module = importlib.import_module("src.libs.splitter")

Chunk = types_module.Chunk
Document = types_module.Document
DocumentChunker = chunker_module.DocumentChunker
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

    document_text = "Alpha intro [image:one] details.\nBeta comparison details."
    image_offset = document_text.index("[image:one]")
    document = Document(
        id="doc-shopping-guide",
        text=document_text,
        metadata={
            "source_path": "shopping_guides/headphones.md",
            "heading_path": ["Guides", "Headphones"],
            "doc_type": "shopping_guide",
            "images": [
                {
                    "id": "image-1",
                    "path": "data/images/shopping_guides/image-1.png",
                    "page": 1,
                    "text_offset": image_offset,
                    "text_length": len("[image:one]"),
                    "position": {"x": 10, "y": 20, "width": 300, "height": 200},
                }
            ],
        },
    )
    chunker = DocumentChunker(
        splitter=FakeSplitter(
            chunks=[
                "Alpha intro [image:one] details.",
                "Beta comparison details.",
            ]
        )
    )

    chunks = chunker.chunk(document)
    repeated_chunks = chunker.chunk(document)

    assert [chunk.chunk_index for chunk in chunks] == [0, 1]
    assert all(isinstance(chunk, Chunk) for chunk in chunks)
    assert [chunk.id for chunk in chunks] == [chunk.id for chunk in repeated_chunks]
    assert chunks[0].metadata["doc_type"] == "shopping_guide"
    assert chunks[0].metadata["image_refs"] == ["image-1"]
    assert chunks[1].metadata["image_refs"] == []
    assert chunks[0].source_ref == {
        "document_id": "doc-shopping-guide",
        "source_path": "shopping_guides/headphones.md",
        "start_offset": 0,
        "end_offset": len("Alpha intro [image:one] details."),
    }
    assert chunks[1].start_offset == document_text.index("Beta comparison details.")
    assert chunks[1].end_offset == len(document_text)
    assert document.metadata.get("image_refs") is None


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
