"""Verify ingestion-owned transform orchestration and chunk enhancement.

The suite protects the C4 boundary where Transform remains an abstract
``libs`` contract while concrete implementations live under ingestion and run
serially according to ``settings.transform.steps``. External LLM calls are
replaced with mocks, while metadata, merge, denoise, identity, and idempotency
behavior execute through the real transform implementations.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

RAG_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures"
SETTINGS_PATH = RAG_ROOT / "config" / "settings.example.yaml"
sys.path.insert(0, str(RAG_ROOT))

config_module = importlib.import_module("src.core.config")
errors_module = importlib.import_module("src.core.errors")
types_module = importlib.import_module("src.core.types")
llm_module = importlib.import_module("src.libs.llm")
transform_contract_module = importlib.import_module("src.libs.transform")
document_summarizer_module = importlib.import_module("src.ingestion.document_summarizer")
transform_module = importlib.import_module("src.ingestion.transform")

Chunk = types_module.Chunk
Document = types_module.Document
IngestionError = errors_module.IngestionError
LLMResponse = llm_module.LLMResponse
BaseTransform = transform_contract_module.BaseTransform
DocumentSummarizer = document_summarizer_module.DocumentSummarizer
ChunkRewriter = transform_module.ChunkRewriter
DenoiseTransform = transform_module.DenoiseTransform
ImageCaptioner = transform_module.ImageCaptioner
ImageToTextTransform = transform_module.ImageToTextTransform
MetadataEnricher = transform_module.MetadataEnricher
SemanticMergeTransform = transform_module.SemanticMergeTransform
TransformPipeline = transform_module.TransformPipeline
load_settings = config_module.load_settings


def make_chunk(
    *,
    chunk_id: str = "chunk-1",
    text: str = "Soft silicone toys are quiet.",
    chunk_index: int = 0,
    start_offset: int = 0,
    section_path: list[str] | None = None,
    metadata: dict[str, object] | None = None,
) -> Chunk:
    """Build one valid business chunk with stable source metadata for tests.

    Args:
        chunk_id: Stable test identity.
        text: Searchable chunk text.
        chunk_index: Position in the synthetic source document.
        start_offset: Start-inclusive synthetic source offset.
        section_path: Optional logical section override.
        metadata: Optional metadata values merged over defaults.

    Returns:
        A validated ``Chunk`` suitable for concrete Transform tests.
    """

    merged_metadata: dict[str, object] = {
        "source_path": "shopping_guides/stress-toys.md",
        "section_path": section_path or ["Stress Toys", "Materials"],
        "image_refs": [],
    }
    merged_metadata.update(metadata or {})
    return Chunk(
        id=chunk_id,
        text=text,
        metadata=merged_metadata,
        chunk_index=chunk_index,
        start_offset=start_offset,
        end_offset=start_offset + len(text),
        source_ref={
            "document_id": "doc-stress-toys",
            "source_path": "shopping_guides/stress-toys.md",
            "section_path": merged_metadata["section_path"],
            "start_offset": start_offset,
            "end_offset": start_offset + len(text),
        },
    )


def test_document_summarizer_generates_top_level_summary_and_is_idempotent() -> None:
    """Require document summaries to be generated once before chunk rewrite."""

    source = Document(
        id="doc-stress-toys",
        text="# Stress Toy Guide\n\nSoft silicone toys are quiet office tools.",
        metadata={"title": "Stress Toy Guide", "collection": "shopping_guides"},
    )
    llm = Mock()
    llm.chat.return_value = LLMResponse(
        content="A buying guide for quiet silicone stress toys used in offices.",
        provider="fake",
        model="fake-summary",
    )
    prompt = config_module.load_prompt("config/prompts/document_summary_prompt.yaml")
    summarizer = DocumentSummarizer(llm=llm, prompt=prompt)

    summarized = summarizer.summarize(source)
    repeated = summarizer.summarize(summarized)

    assert summarized.summary == (
        "A buying guide for quiet silicone stress toys used in offices."
    )
    assert summarized.metadata["summary_generation"]["provider"] == "fake"
    assert source.summary is None
    assert repeated == summarized
    llm.chat.assert_called_once()


def test_transform_pipeline_builds_enabled_steps_from_settings_order() -> None:
    """Require settings step names to build a serial ingestion transform chain."""

    settings = load_settings(
        SETTINGS_PATH,
        environ={},
        validate_environment=False,
    )
    fake_llm = Mock()

    pipeline = TransformPipeline.from_settings(settings=settings, llm=fake_llm)

    assert [type(transform).__name__ for transform in pipeline.transforms] == [
        "MetadataEnricher",
        "ChunkRewriter",
        "SemanticMergeTransform",
        "DenoiseTransform",
        "ImageCaptioner",
    ]
    assert all(isinstance(transform, BaseTransform) for transform in pipeline.transforms)


def test_transform_pipeline_runs_steps_serially_without_factory_provider() -> None:
    """Require ingestion orchestration to execute configured steps in order."""

    class AppendTransform(BaseTransform):
        """Append one marker to prove serial execution order."""

        def __init__(self, marker: str) -> None:
            self._marker = marker

        def transform(
            self,
            chunks: list[Chunk],
            *,
            context: dict[str, object] | None = None,
        ) -> list[Chunk]:
            del context
            return [
                chunk.model_copy(update={"text": f"{chunk.text}{self._marker}"}, deep=True)
                for chunk in chunks
            ]

    pipeline = TransformPipeline([AppendTransform("-a"), AppendTransform("-b")])

    result = pipeline.run([make_chunk(text="source")])

    assert result[0].text == "source-a-b"


def test_transform_pipeline_rejects_unknown_enabled_steps() -> None:
    """Require invalid transform step names to fail before ingestion starts."""

    settings = load_settings(
        SETTINGS_PATH,
        environ={},
        validate_environment=False,
    )
    settings.transform.steps.append(config_module.TransformStepSettings(name="missing"))

    with pytest.raises(errors_module.ConfigurationError, match="Unknown transform step"):
        TransformPipeline.from_settings(settings=settings, llm=Mock())


def test_metadata_enricher_adds_context_without_mutating_input() -> None:
    """Require context enrichment to preserve source-owned metadata values."""

    source = make_chunk(metadata={"collection": "shopping_guides"})
    transform = MetadataEnricher()

    result = transform.transform(
        [source],
        context={
            "title": "Stress Toy Guide",
            "topic": "quiet office stress relief",
            "collection": "untrusted-override",
        },
    )

    assert result[0] is not source
    assert result[0].metadata["title"] == "Stress Toy Guide"
    assert result[0].metadata["topic"] == "quiet office stress relief"
    assert result[0].metadata["collection"] == "shopping_guides"
    assert "title" not in source.metadata


def test_chunk_rewriter_uses_llm_and_is_idempotent() -> None:
    """Require chunk rewrite to use document summaries as added context."""

    source = make_chunk()
    llm = Mock()
    llm.chat.return_value = LLMResponse(
        content="Soft silicone stress toys provide quiet tactile feedback.",
        provider="fake",
        model="fake-rewriter",
    )
    prompt = config_module.load_prompt("config/prompts/rewrite_chunk_prompt.yaml")
    transform = ChunkRewriter(llm=llm, prompt=prompt)

    rewritten = transform.transform(
        [source],
        context={
            "document_summary": (
                "The source document explains quiet stress-relief products for offices."
            ),
        },
    )
    repeated = transform.transform(rewritten)

    assert rewritten[0].text == (
        "Soft silicone stress toys provide quiet tactile feedback."
    )
    assert rewritten[0].id != source.id
    assert rewritten[0].start_offset == source.start_offset
    assert rewritten[0].end_offset == source.end_offset
    assert rewritten[0].metadata["rewrite"]["provider"] == "fake"
    assert repeated == rewritten
    llm.chat.assert_called_once()
    user_message = llm.chat.call_args.args[0][1].content
    assert "Document summary:" in user_message
    assert "quiet stress-relief products" in user_message


def test_semantic_merge_combines_adjacent_chunks_and_is_idempotent() -> None:
    """Require an affirmative LLM decision to merge one logical section."""

    first = make_chunk(
        chunk_id="chunk-a",
        text="Silicone models are quiet.",
        chunk_index=0,
        start_offset=0,
        metadata={"image_refs": ["image-1"]},
    )
    second = make_chunk(
        chunk_id="chunk-b",
        text="They are suitable for offices.",
        chunk_index=1,
        start_offset=30,
        metadata={"image_refs": ["image-2"]},
    )
    llm = Mock()
    llm.chat.return_value = LLMResponse(
        content=json.dumps(
            {
                "merge": True,
                "merged_text": (
                    "Silicone models are quiet and suitable for offices."
                ),
            }
        ),
        provider="fake",
        model="fake-merge",
    )
    prompt = config_module.load_prompt("config/prompts/semantic_merge_prompt.yaml")
    transform = SemanticMergeTransform(llm=llm, prompt=prompt)

    merged = transform.transform([first, second])
    repeated = transform.transform(merged)

    assert len(merged) == 1
    assert merged[0].text == (
        "Silicone models are quiet and suitable for offices."
    )
    assert merged[0].metadata["image_refs"] == ["image-1", "image-2"]
    assert merged[0].start_offset == first.start_offset
    assert merged[0].end_offset == second.end_offset
    assert merged[0].source_ref["end_offset"] == second.end_offset
    assert merged[0].chunk_index == 0
    assert merged[0].metadata["chunk_index"] == 0
    assert repeated == merged
    llm.chat.assert_called_once()


def test_semantic_merge_does_not_compare_different_sections() -> None:
    """Require section boundaries to prevent unrelated LLM merge requests."""

    first = make_chunk(section_path=["Stress Toys", "Materials"])
    second = make_chunk(
        chunk_id="chunk-2",
        text="Compare prices before ordering.",
        chunk_index=1,
        start_offset=40,
        section_path=["Stress Toys", "Budget"],
    )
    llm = Mock()
    prompt = config_module.load_prompt("config/prompts/semantic_merge_prompt.yaml")

    result = SemanticMergeTransform(llm=llm, prompt=prompt).transform(
        [first, second]
    )

    assert [chunk.text for chunk in result] == [first.text, second.text]
    llm.chat.assert_not_called()


def test_semantic_merge_wraps_llm_failures_as_ingestion_errors() -> None:
    """Require provider failures to preserve the BaseTransform error contract."""

    first = make_chunk(chunk_id="chunk-a", text="First partial idea.")
    second = make_chunk(
        chunk_id="chunk-b",
        text="Continuation of the same idea.",
        chunk_index=1,
        start_offset=30,
    )
    llm = Mock()
    llm.chat.side_effect = RuntimeError("provider unavailable")
    prompt = config_module.load_prompt("config/prompts/semantic_merge_prompt.yaml")

    with pytest.raises(IngestionError, match="Unable to evaluate semantic merge"):
        SemanticMergeTransform(llm=llm, prompt=prompt).transform([first, second])


def test_denoise_transform_cleans_typical_parser_noise_and_preserves_images() -> None:
    """Require deterministic cleanup of the approved noisy-document fixture."""

    noisy_text = (
        FIXTURES_ROOT / "noisy_documents" / "parsed_guide.txt"
    ).read_text(encoding="utf-8")
    source = make_chunk(text=noisy_text)
    transform = DenoiseTransform()

    cleaned = transform.transform([source])
    repeated = transform.transform(cleaned)

    assert "SHOPPING GUIDE" not in cleaned[0].text
    assert "Table of Contents" not in cleaned[0].text
    assert "Page 3 / 12" not in cleaned[0].text
    assert "--------------------" not in cleaned[0].text
    assert "suitable for quiet office use." in cleaned[0].text
    assert "[[image:image-stress-ball]]" in cleaned[0].text
    assert cleaned[0].metadata["denoise"]["removed_line_count"] >= 4
    assert repeated == cleaned


def test_denoise_preserves_repeated_content_outside_document_boundaries() -> None:
    """Require repeated recommendations in the body to remain searchable."""

    source = make_chunk(
        text=(
            "Introduction.\n\n"
            "Check the material label.\n\n"
            "Compare dimensions.\n\n"
            "Check the material label.\n\n"
            "2026\n\n"
            "Conclusion."
        )
    )

    cleaned = DenoiseTransform().transform([source])

    assert cleaned[0].text.count("Check the material label.") == 2
    assert "2026" in cleaned[0].text


def test_image_captioner_generates_metadata_for_referenced_images() -> None:
    """Require enabled image captioning to enrich chunks with structured metadata.

    The ingestion pipeline keeps image captions in metadata so later Dense and
    BM25 steps can decide how to compose searchable text without coupling the
    captioner to indexing internals. A failure here means image references are
    no longer converted into retrievable caption data.
    """

    source = make_chunk(
        text="[[image:image-stress-ball]]\nSoft silicone models are quiet.",
        metadata={
            "image_refs": ["image-stress-ball"],
            "images": [
                {
                    "id": "image-stress-ball",
                    "path": "data/images/shopping_guides/image-stress-ball.png",
                    "page": 1,
                    "text_offset": 0,
                    "text_length": len("[[image:image-stress-ball]]"),
                    "position": {"width": 640, "height": 480},
                }
            ],
        },
    )
    vision_llm = Mock()
    vision_llm.chat.return_value = LLMResponse(
        content=json.dumps(
            {
                "status": "success",
                "description": "图片展示一个蓝色硅胶解压球，表面为磨砂材质。",
                "extracted_text": "",
                "key_facts": ["蓝色硅胶", "磨砂材质"],
                "reason": "",
            },
            ensure_ascii=False,
        ),
        provider="fake-vision",
        model="fake-vl",
    )
    prompt = config_module.load_prompt("config/prompts/image_to_text_prompt.yaml")

    captioner = ImageCaptioner(
        image_transform=ImageToTextTransform(vision_llm=vision_llm, prompt=prompt),
        enabled=True,
    )
    captioned = captioner.caption([source], context={"document_context": "stress toy guide"})

    assert captioned[0] is not source
    assert captioned[0].text == source.text
    assert captioned[0].metadata["image_caption_status"] == "success"
    assert captioned[0].metadata["image_captions"] == [
        {
            "image_id": "image-stress-ball",
            "status": "success",
            "description": "图片展示一个蓝色硅胶解压球，表面为磨砂材质。",
            "extracted_text": "",
            "key_facts": ["蓝色硅胶", "磨砂材质"],
            "reason": "",
            "provider": "fake-vision",
            "model": "fake-vl",
        }
    ]
    vision_llm.chat.assert_called_once()


def test_image_captioner_skips_when_disabled_without_calling_vision_llm() -> None:
    """Require disabled vision captioning to be visible and side-effect free."""

    source = make_chunk(metadata={"image_refs": ["image-1"], "images": []})
    image_transform = Mock()
    captioner = ImageCaptioner(image_transform=image_transform, enabled=False)

    captioned = captioner.caption([source])

    assert captioner.should_caption(source) is False
    assert captioned[0].metadata["image_caption_status"] == "skipped"
    image_transform.transform.assert_not_called()


def test_image_captioner_ignores_chunks_without_image_refs() -> None:
    """Require text-only chunks to avoid noisy caption metadata."""

    source = make_chunk(metadata={"image_refs": []})
    image_transform = Mock()

    captioned = ImageCaptioner(image_transform=image_transform, enabled=True).caption(
        [source]
    )

    assert "image_caption_status" not in captioned[0].metadata
    assert "image_captions" not in captioned[0].metadata
    image_transform.transform.assert_not_called()


def test_transform_pipeline_respects_disabled_vision_settings() -> None:
    """Require settings.vision_llm.enabled to gate image caption execution."""

    settings = load_settings(
        SETTINGS_PATH,
        environ={},
        validate_environment=False,
    )
    settings.vision_llm.enabled = False
    text_llm = Mock()
    text_llm.chat.side_effect = [
        LLMResponse(
            content="Soft silicone models are quiet.",
            provider="fake",
            model="fake-rewriter",
        ),
        LLMResponse(
            content=json.dumps({"merge": False}),
            provider="fake",
            model="fake-merge",
        ),
    ]
    vision_llm = Mock()
    vision_llm.chat.return_value = LLMResponse(
        content=json.dumps(
            {
                "status": "success",
                "description": "图片展示一个蓝色硅胶解压球。",
                "extracted_text": "",
                "key_facts": [],
                "reason": "",
            },
            ensure_ascii=False,
        ),
        provider="fake-vision",
        model="fake-vl",
    )
    source = make_chunk(
        text="[[image:image-1]]\nSoft silicone models are quiet.",
        metadata={
            "image_refs": ["image-1"],
            "images": [
                {
                    "id": "image-1",
                    "path": "data/images/shopping_guides/image-1.png",
                    "page": 1,
                    "text_offset": 0,
                    "text_length": len("[[image:image-1]]"),
                    "position": {"width": 640, "height": 480},
                }
            ],
        },
    )

    pipeline = TransformPipeline.from_settings(
        settings=settings,
        llm=text_llm,
        vision_llm=vision_llm,
    )
    transformed = pipeline.run([source])

    assert transformed[0].metadata["image_caption_status"] == "skipped"
    vision_llm.chat.assert_not_called()


def test_image_captioner_records_failed_and_low_quality_results() -> None:
    """Require failed or unusable image understanding to preserve the chunk."""

    failed_chunk = make_chunk(
        chunk_id="chunk-failed",
        metadata={
            "image_refs": ["image-1"],
            "images": [
                {
                    "id": "image-1",
                    "path": "data/images/shopping_guides/image-1.png",
                    "page": 1,
                    "text_offset": 0,
                    "text_length": 17,
                    "position": {"width": 640, "height": 480},
                }
            ],
        },
    )
    low_quality_chunk = make_chunk(
        chunk_id="chunk-low-quality",
        metadata={
            "image_refs": ["image-2"],
            "images": [
                {
                    "id": "image-2",
                    "path": "data/images/shopping_guides/image-2.png",
                    "page": 2,
                    "text_offset": 0,
                    "text_length": 17,
                    "position": {"width": 20, "height": 20},
                }
            ],
        },
    )
    image_transform = Mock()
    image_transform.transform.side_effect = [
        RuntimeError("vision unavailable"),
        {
            "status": "success",
            "description": "太短",
            "extracted_text": "",
            "key_facts": [],
            "reason": "",
            "provider": "fake-vision",
            "model": "fake-vl",
        },
    ]

    captioned = ImageCaptioner(image_transform=image_transform, enabled=True).caption(
        [failed_chunk, low_quality_chunk]
    )

    assert captioned[0].metadata["image_caption_status"] == "failed"
    assert captioned[0].metadata["image_captions"][0]["status"] == "failed"
    assert captioned[0].text == failed_chunk.text
    assert captioned[1].metadata["image_caption_status"] == "low_quality"
    assert captioned[1].metadata["image_captions"][0]["status"] == "low_quality"
