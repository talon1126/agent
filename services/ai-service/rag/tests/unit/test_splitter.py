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
SplitterFactory = splitter_module.SplitterFactory


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
    compute source offsets, build source references, distribute image IDs by
    scanning placeholders in chunk text, and return validated ``Chunk`` objects.
    """

    document_text = (
        "Alpha intro [[image:image-1]] details.\n"
        "Beta comparison details.\n"
        "Gamma outro [[image:image-2]] details."
    )
    first_image = {
        "id": "image-1",
        "path": "data/images/shopping_guides/image-1.png",
    }
    second_image = {
        "id": "image-2",
        "path": "data/images/shopping_guides/image-2.png",
    }
    document = Document(
        id="doc-shopping-guide",
        text=document_text,
        metadata={
            "source_path": "shopping_guides/headphones.md",
            "collection": "shopping_guides",
            "title": "Headphones",
            "source_type": "markdown",
            "source_hash": "hash-1",
            "heading_path": ["Guides", "Headphones"],
            "doc_type": "shopping_guide",
            "images": [first_image, second_image],
        },
    )
    chunker = DocumentChunker(
        splitter=FakeSplitter(
            chunks=[
                "Alpha intro [[image:image-1]] details.",
                "Beta comparison details.",
                "Gamma outro [[image:image-2]] details.",
            ]
        )
    )

    chunks = chunker.chunk(document)
    repeated_chunks = chunker.chunk(document)

    assert [chunk.chunk_index for chunk in chunks] == [0, 1, 2]
    assert all(isinstance(chunk, Chunk) for chunk in chunks)
    assert [chunk.id for chunk in chunks] == [chunk.id for chunk in repeated_chunks]
    assert document.metadata["images"] == [first_image, second_image]
    assert chunks[0].metadata == {
        "collection": "shopping_guides",
        "document_id": "doc-shopping-guide",
        "source_path": "shopping_guides/headphones.md",
        "doc_type": "shopping_guide",
        "topic": "Headphones",
        "chunk_index": 0,
        "section_path": ["Guides", "Headphones"],
        "image_refs": ["image-1"],
    }
    assert chunks[0].metadata["doc_type"] == "shopping_guide"
    assert "image_refs" not in chunks[1].metadata
    assert chunks[2].metadata["image_refs"] == ["image-2"]
    for chunk in chunks:
        assert not hasattr(chunk, "source_ref")
        assert "images" not in chunk.metadata
        assert "headings" not in chunk.metadata
        assert "source_type" not in chunk.metadata
        assert "source_hash" not in chunk.metadata
        assert "title" not in chunk.metadata

    assert chunks[1].start_offset == document_text.index("Beta comparison details.")
    assert chunks[2].start_offset == document_text.index("Gamma outro")
    assert chunks[2].end_offset == len(document_text)
    assert document.metadata.get("image_refs") is None


def test_document_chunker_merges_trailing_image_only_chunk_into_previous_text() -> None:
    """Require extracted images to remain attached to retrievable text chunks."""

    placeholder = "[[image:image-headphones]]"
    document_text = f"Wireless headphone buying guide.\n\n{placeholder}"
    document = Document(
        id="doc-image-tail",
        text=document_text,
        metadata={
            "source_path": "shopping_guides/headphones.pdf",
            "images": [
                {
                    "id": "image-headphones",
                    "path": "data/images/image-headphones.png",
                }
            ],
        },
    )
    chunker = DocumentChunker(
        splitter=FakeSplitter(
            chunks=[
                "Wireless headphone buying guide.",
                placeholder,
            ]
        )
    )

    chunks = chunker.chunk(document)

    assert len(chunks) == 1
    assert chunks[0].text == document_text
    assert chunks[0].metadata["image_refs"] == ["image-headphones"]
    assert chunks[0].end_offset == len(document_text)


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
    document. DocumentChunker must select the active H2-plus section preceding
    each source range instead of copying one document-wide path to every chunk.
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

    assert [chunk.metadata.get("section_path") for chunk in chunks] == [
        None,
        ["Gaming"],
        ["Commuting"],
    ]
    assert len({chunk.id for chunk in chunks}) == 3
    assert chunks[0].metadata["source_path"] == "shopping_guides/headphones.md"
    assert chunks[1].metadata["section_path"] == ["Gaming"]


def test_document_chunker_section_path_starts_at_h2_without_section_object() -> None:
    """Require chunk section metadata to contain only an H2-plus path.

    Loader heading metadata keeps the full Markdown path, including the document
    H1 title. Chunk metadata should treat that H1 as document context rather
    than section context, so only H2/H3/H4 titles are retained in
    ``section_path``. The adapter must not introduce a duplicate nested
    ``section`` object or individual h2/h3/h4 fields.
    """

    document_text = (
        "# Phone Guide\n"
        "Document introduction.\n"
        "## Brand Database\n"
        "Database overview.\n"
        "### Apple\n"
        "Apple buying details.\n"
        "#### Camera\n"
        "Camera-specific advice."
    )
    document = Document(
        id="doc-section-path",
        text=document_text,
        metadata={
            "source_path": "shopping_guides/phones.md",
            "headings": [
                {
                    "level": 1,
                    "title": "Phone Guide",
                    "path": ["Phone Guide"],
                    "text_offset": document_text.index("# Phone Guide"),
                },
                {
                    "level": 2,
                    "title": "Brand Database",
                    "path": ["Phone Guide", "Brand Database"],
                    "text_offset": document_text.index("## Brand Database"),
                },
                {
                    "level": 3,
                    "title": "Apple",
                    "path": ["Phone Guide", "Brand Database", "Apple"],
                    "text_offset": document_text.index("### Apple"),
                },
                {
                    "level": 4,
                    "title": "Camera",
                    "path": ["Phone Guide", "Brand Database", "Apple", "Camera"],
                    "text_offset": document_text.index("#### Camera"),
                },
            ],
        },
    )
    chunks = DocumentChunker(
        splitter=FakeSplitter(
            chunks=[
                "Document introduction.",
                "Database overview.",
                "Apple buying details.",
                "Camera-specific advice.",
            ]
        )
    ).chunk(document)

    assert chunks[0].metadata.get("section_path") is None
    assert chunks[1].metadata["section_path"] == ["Brand Database"]
    assert chunks[2].metadata["section_path"] == ["Brand Database", "Apple"]
    assert chunks[3].metadata["section_path"] == ["Brand Database", "Apple", "Camera"]
    for chunk in chunks:
        assert "section" not in chunk.metadata
        assert "h2" not in chunk.metadata
        assert "h3" not in chunk.metadata
        assert "h4" not in chunk.metadata

def test_document_chunker_deep_copies_document_metadata() -> None:
    """Require each chunk to own independent retained metadata structures.

    Later transform stages may update retained business metadata such as
    ``section_path``. Sharing nested lists with sibling chunks would make one
    transform silently alter unrelated pipeline objects.
    """

    document = Document(
        id="doc-metadata-copy",
        text="First section.\nSecond section.",
        metadata={
            "source_path": "shopping_guides/copy.md",
            "heading_path": ["Copy", "Guide"],
        },
    )
    chunks = DocumentChunker(
        splitter=FakeSplitter(chunks=["First section.", "Second section."])
    ).chunk(document)

    chunks[0].metadata["section_path"].append("mutated")

    assert document.metadata["heading_path"] == ["Copy", "Guide"]
    assert chunks[1].metadata["section_path"] == ["Copy", "Guide"]


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
    assert chunks[0].metadata["document_id"] == "doc-step"


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


def test_markdown_section_splitter_keeps_short_section_with_following_sibling() -> None:
    """Require Markdown section splitting to avoid title-only chunks."""

    markdown_module = importlib.import_module("src.libs.splitter.markdown_section_splitter")
    splitter = markdown_module.MarkdownSectionSplitter(
        chunk_size=260,
        chunk_overlap=40,
        min_section_chars=80,
    )
    text = (
        "# Phone Guide\n\n"
        "## Brands\n\n"
        "### Apple\n\n"
        "Short note.\n\n"
        "### Samsung\n\n"
        "Samsung has strong screens, update policy, camera hardware, and Android "
        "ecosystem support for premium buyers.\n\n"
        "### Xiaomi\n\n"
        "Xiaomi focuses on charging, hardware value, camera partnership, and smart "
        "home integration."
    )

    parts = splitter.split(text)

    assert all(part.strip() != "### Apple" for part in parts)
    assert any("Short note." in part and "Samsung has strong screens" in part for part in parts)
    assert all(len(part) <= 260 for part in parts)


def test_markdown_section_splitter_splits_long_table_with_repeated_header() -> None:
    """Require long Markdown tables to split by rows while keeping headers."""

    markdown_module = importlib.import_module("src.libs.splitter.markdown_section_splitter")
    splitter = markdown_module.MarkdownSectionSplitter(
        chunk_size=360,
        chunk_overlap=0,
        min_section_chars=40,
    )
    rows = "\n".join(
        f"| Model {index} | Flagship phone with detailed specs {index} | OLED display |"
        for index in range(1, 10)
    )
    text = (
        "# Phone Guide\n\n"
        "## Brand Database\n\n"
        "### Samsung\n\n"
        "Samsung section introduction keeps brand context.\n\n"
        "| Model | Positioning | Display |\n"
        "| --- | --- | --- |\n"
        f"{rows}\n\n"
        "Samsung buying advice after the table."
    )

    parts = splitter.split(text)
    table_parts = [part for part in parts if "| Model " in part]

    assert len(table_parts) >= 2
    for part in table_parts:
        assert "| Model | Positioning | Display |" in part
        assert "| --- | --- | --- |" in part
        assert len(part) <= 360


def test_markdown_section_splitter_splits_long_sections_without_heading_text() -> None:
    """Require section overflow chunks to keep body text free of headings."""

    markdown_module = importlib.import_module("src.libs.splitter.markdown_section_splitter")
    splitter = markdown_module.MarkdownSectionSplitter(
        chunk_size=220,
        chunk_overlap=30,
        min_section_chars=40,
    )
    paragraph = (
        "Apple buyers should compare storage, camera features, video workflow, "
        "battery life, and ecosystem lock-in before choosing a model. "
    )
    text = "# Phone Guide\n\n## Brands\n\n### Apple\n\n" + paragraph * 7

    parts = splitter.split(text)

    assert len(parts) > 1
    assert all("### Apple" not in part for part in parts)
    assert all(len(part) <= 220 for part in parts)


def test_markdown_section_splitter_registered_in_factory() -> None:
    """Require the Markdown splitter to be selectable by configuration name."""

    splitter = SplitterFactory.create(
        provider="markdown_section",
        chunk_size=220,
        chunk_overlap=20,
        min_section_chars=40,
    )

    assert splitter.__class__.__name__ == "MarkdownSectionSplitter"


def test_markdown_section_splitter_merges_table_tail_with_buying_advice() -> None:
    """Require short table-tail chunks to merge with following advice blocks."""

    markdown_module = importlib.import_module("src.libs.splitter.markdown_section_splitter")
    splitter = markdown_module.MarkdownSectionSplitter(
        chunk_size=520,
        chunk_overlap=0,
        min_section_chars=40,
    )
    text = (
        "# Phone Guide\n\n"
        "## Brands\n\n"
        "### Apple\n\n"
        "Apple overview.\n\n"
        "| Model | Positioning | Display |\n"
        "| --- | --- | --- |\n"
        "| iPhone 17 Pro Max | Large flagship | OLED |\n"
        "| iPhone 17 Pro | Flagship | OLED |\n"
        "| iPhone 16e | Entry iPhone | OLED |\n\n"
        "Buying advice:\n\n"
        "- Choose Pro Max for battery and video.\n"
        "- Choose 16e for lower budget."
    )

    parts = splitter.split(text)

    assert len(parts) == 1
    assert "| iPhone 16e | Entry iPhone | OLED |" in parts[0]
    assert "Buying advice:" in parts[0]


def test_markdown_section_splitter_pairs_final_table_rows_with_buying_advice() -> None:
    """Require long tables to keep final rows and advice in the same chunk."""

    markdown_module = importlib.import_module("src.libs.splitter.markdown_section_splitter")
    splitter = markdown_module.MarkdownSectionSplitter(
        chunk_size=760,
        chunk_overlap=0,
        min_section_chars=40,
    )
    text = (
        "# Phone Guide\n\n"
        "## Brands\n\n"
        "### Apple\n\n"
        "Apple overview for buyers who compare video, battery, storage, and ecosystem.\n\n"
        "| Model | Positioning | Display | Storage | Audience |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| iPhone 17 Pro Max | Large flagship with the strongest battery and "
        "video workflow | OLED ProMotion | 256GB/512GB/1TB/2TB | "
        "Creators and heavy users |\n"
        "| iPhone 17 Pro | Compact flagship with Pro camera features | "
        "OLED ProMotion | 256GB/512GB/1TB | Users who want flagship power |\n"
        "| iPhone 17 | Mainstream iPhone with balanced camera and battery | "
        "OLED high refresh | 128GB/256GB/512GB | Most iOS buyers |\n"
        "| iPhone Air | Thin model that prioritizes hand feel over maximum battery | "
        "OLED | 256GB/512GB/1TB | Light phone buyers |\n"
        "| iPhone 16e | Entry iPhone for lower budgets | OLED | "
        "128GB/256GB/512GB | Budget-sensitive iOS buyers |\n\n"
        "Buying advice:\n\n"
        "- Choose Pro Max for battery and video.\n"
        "- Choose 16e when budget matters."
    )

    parts = splitter.split(text)

    advice_parts = [part for part in parts if "Buying advice:" in part]
    assert len(advice_parts) == 1
    assert "| Model | Positioning | Display | Storage | Audience |" in advice_parts[0]
    assert "| iPhone 16e | Entry iPhone for lower budgets" in advice_parts[0]
    assert "- Choose 16e when budget matters." in advice_parts[0]
    assert all(not part.lstrip().startswith("#") for part in parts)


def test_markdown_section_splitter_does_not_repeat_intro_after_table_split() -> None:
    """Require only the first split table chunk to keep the intro paragraph.

    The first table fragment may carry the paragraph that introduces the table,
    but later table fragments should contain only the repeated table header,
    data rows, and optional advice tail. Repeating the intro in every table
    fragment increases embedding noise and caused duplicated context in the
    exported shopping guide chunks.
    """

    markdown_module = importlib.import_module("src.libs.splitter.markdown_section_splitter")
    splitter = markdown_module.MarkdownSectionSplitter(
        chunk_size=650,
        chunk_overlap=0,
        min_section_chars=40,
    )
    intro = (
        "Apple overview explains that iPhone buyers should compare video, "
        "battery, storage, ecosystem, warranty, and long-term updates before "
        "selecting a model."
    )
    text = (
        "# Phone Guide\n\n"
        "## Brands\n\n"
        "### Apple\n\n"
        f"{intro}\n\n"
        "| Model | Positioning | Main benefit | Audience |\n"
        "| --- | --- | --- | --- |\n"
        "| iPhone 17 Pro Max | Large flagship | Best battery and video workflow | "
        "Heavy creators |\n"
        "| iPhone 17 Pro | Compact flagship | Pro camera in smaller size | Flagship users |\n"
        "| iPhone 17 | Balanced mainstream model | Camera and battery balance | Most buyers |\n"
        "| iPhone Air | Thin design model | Lighter hand feel | Thin phone buyers |\n"
        "| iPhone 16e | Entry iPhone | Lower iOS entry cost | Budget buyers |\n\n"
        "Buying advice:\n\n"
        "- Choose Pro Max for battery and video.\n"
        "- Choose 16e when budget is the main constraint.\n"
        "- Avoid low storage for heavy photo and video users."
    )

    parts = splitter.split(text)
    table_parts = [part for part in parts if "| Model | Positioning |" in part]

    assert len(table_parts) >= 2
    assert sum(intro in part for part in table_parts) == 1
    assert "Buying advice:" in table_parts[-1]
    assert intro not in table_parts[-1]


def test_markdown_section_splitter_removes_heading_lines_from_all_chunks() -> None:
    """Require every chunk body to omit Markdown ATX heading lines."""

    markdown_module = importlib.import_module("src.libs.splitter.markdown_section_splitter")
    splitter = markdown_module.MarkdownSectionSplitter(
        chunk_size=420,
        chunk_overlap=0,
        min_section_chars=120,
    )
    text = (
        "# Phone Guide\n\n"
        "Intro paragraph before the first target section.\n\n"
        "## Buying Basics\n\n"
        "Basics paragraph that should remain without its heading.\n\n"
        "### Apple\n\n"
        "Short note.\n\n"
        "### Samsung\n\n"
        "Samsung paragraph that merges with the short Apple section."
    )

    parts = splitter.split(text)

    assert parts
    assert not any(
        line.lstrip().startswith("#")
        for part in parts
        for line in part.splitlines()
    )
    assert any("Basics paragraph" in part for part in parts)
    assert any("Short note." in part and "Samsung paragraph" in part for part in parts)


def test_markdown_section_splitter_does_not_inject_heading_context_in_chunks() -> None:
    """Require heading context to live in metadata rather than chunk text."""

    markdown_module = importlib.import_module("src.libs.splitter.markdown_section_splitter")
    splitter = markdown_module.MarkdownSectionSplitter(
        chunk_size=260,
        chunk_overlap=0,
        min_section_chars=40,
    )
    text = (
        "# Phone Guide\n\n"
        "## Brands\n\n"
        "### Apple\n\n"
        "Apple buyers should compare storage, camera, battery, and ecosystem. " * 6
    )

    parts = splitter.split(text)

    assert len(parts) > 1
    assert all(not part.lstrip().startswith("#") for part in parts)
    assert all("### Apple" not in part for part in parts)
