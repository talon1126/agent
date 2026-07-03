"""Verify ingestion-owned transform orchestration and chunk enhancement.

The suite protects the C4 boundary where Transform remains an abstract
``libs`` contract while concrete implementations live under ingestion and run
serially according to ``settings.transform.steps``. External LLM calls are
replaced with mocks, while metadata, merge, denoise, identity, and idempotency
behavior execute through the real transform implementations.
"""

from __future__ import annotations

import base64
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
ProviderError = errors_module.ProviderError
LLMResponse = llm_module.LLMResponse
VisionCaptionResponse = llm_module.VisionCaptionResponse
DashScopeVisionLLM = llm_module.DashScopeVisionLLM
BaseTransform = transform_contract_module.BaseTransform
DocumentSummarizer = document_summarizer_module.DocumentSummarizer
ChunkRewriter = transform_module.ChunkRewriter
DenoiseTransform = transform_module.DenoiseTransform
ImageCaptioner = transform_module.ImageCaptioner
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
        "document_id": "doc-stress-toys",
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


def test_transform_pipeline_reports_each_implementation_timing() -> None:
    """The pipeline should report ordered timing and chunk counts per step."""

    class AppendTransform(BaseTransform):
        """Append one suffix while preserving the number of chunks."""

        def transform(
            self,
            chunks: list[Chunk],
            *,
            context: dict[str, object] | None = None,
        ) -> list[Chunk]:
            del context
            return [
                chunk.model_copy(update={"text": f"{chunk.text}-enhanced"})
                for chunk in chunks
            ]

    class DropLastTransform(BaseTransform):
        """Remove the final chunk to expose output-count changes."""

        def transform(
            self,
            chunks: list[Chunk],
            *,
            context: dict[str, object] | None = None,
        ) -> list[Chunk]:
            del context
            return list(chunks[:-1])

    records: list[dict[str, object]] = []
    pipeline = TransformPipeline([AppendTransform(), DropLastTransform()])

    result = pipeline.run(
        [make_chunk(chunk_id="chunk-1"), make_chunk(chunk_id="chunk-2")],
        step_observer=records.append,
        snapshot_options={
            "enabled": True,
            "max_chunks_per_step": 10,
            "max_chars_per_chunk": 12,
            "include_unchanged_chunks": False,
        },
    )

    assert len(result) == 1
    assert [record["name"] for record in records] == [
        "append_transform",
        "drop_last_transform",
    ]
    assert [record["provider"] for record in records] == [
        "AppendTransform",
        "DropLastTransform",
    ]
    assert [(record["input_count"], record["output_count"]) for record in records] == [
        (2, 2),
        (2, 1),
    ]
    assert records[0]["snapshots"] == [
        {
            "chunk_id": "chunk-1",
            "chunk_index": 0,
            "change_type": "changed",
            "before_preview": "Soft silicon",
            "after_preview": "Soft silicon",
            "before_truncated": True,
            "after_truncated": True,
        },
        {
            "chunk_id": "chunk-2",
            "chunk_index": 0,
            "change_type": "changed",
            "before_preview": "Soft silicon",
            "after_preview": "Soft silicon",
            "before_truncated": True,
            "after_truncated": True,
        },
    ]
    assert records[1]["snapshots"] == [
        {
            "chunk_id": "chunk-2",
            "chunk_index": 0,
            "change_type": "removed",
            "before_preview": "Soft silicon",
            "after_preview": "",
            "before_truncated": True,
            "after_truncated": False,
        }
    ]
    assert records[0]["changed_count"] == 2
    assert records[0]["unchanged_count"] == 0
    assert records[1]["changed_count"] == 1
    assert records[1]["unchanged_count"] == 1
    assert all(record["status"] == "success" for record in records)
    assert all(float(record["duration_ms"]) >= 0 for record in records)


def test_transform_pipeline_reports_failed_implementation_before_raising() -> None:
    """A failed transform should emit its timing record before propagation."""

    class FailingTransform(BaseTransform):
        """Raise a deterministic provider-like failure."""

        def transform(
            self,
            chunks: list[Chunk],
            *,
            context: dict[str, object] | None = None,
        ) -> list[Chunk]:
            del chunks, context
            raise RuntimeError("transform failed")

    records: list[dict[str, object]] = []

    with pytest.raises(RuntimeError, match="transform failed"):
        TransformPipeline([FailingTransform()]).run(
            [make_chunk()],
            step_observer=records.append,
        )

    assert len(records) == 1
    assert records[0]["name"] == "failing_transform"
    assert records[0]["provider"] == "FailingTransform"
    assert records[0]["status"] == "failed"
    assert records[0]["input_count"] == 1
    assert records[0]["output_count"] == 0
    assert float(records[0]["duration_ms"]) >= 0
    assert records[0]["error"] == {
        "error_type": "RuntimeError",
        "message": "transform failed",
    }


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
    assert result[0].metadata["topic"] == "quiet office stress relief"
    assert result[0].metadata["collection"] == "shopping_guides"
    assert "title" not in source.metadata
    assert "title" not in result[0].metadata


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
    assert "rewrite" not in rewritten[0].metadata
    assert repeated[0].text == rewritten[0].text
    assert "rewrite" not in repeated[0].metadata
    assert llm.chat.call_count == 2
    user_message = llm.chat.call_args_list[0].args[0][1].content
    assert "Document summary:" in user_message
    assert "quiet stress-relief products" in user_message
    assert "Metadata:" not in user_message
    assert "Image references:" not in user_message
    assert "shopping_guides/stress-toys.md" not in user_message
    assert "image_refs" not in user_message


def test_chunk_rewriter_extracts_text_from_structured_llm_response() -> None:
    """Require rewrite JSON payloads to keep metadata out of chunk text."""

    source = make_chunk(metadata={"collection": "shopping_guides"})
    llm = Mock()
    llm.chat.return_value = LLMResponse(
        content=json.dumps(
            {
                "text": "Soft silicone stress toys provide quiet tactile feedback.",
                "metadata": {"collection": "shopping_guides"},
                "image_refs": [],
            },
            ensure_ascii=False,
        ),
        provider="fake",
        model="fake-rewriter",
    )
    transform = ChunkRewriter(
        llm=llm,
        prompt=config_module.load_prompt("config/prompts/rewrite_chunk_prompt.yaml"),
    )

    rewritten = transform.transform([source])

    assert rewritten[0].text == (
        "Soft silicone stress toys provide quiet tactile feedback."
    )
    assert "metadata" not in rewritten[0].text.lower()
    assert rewritten[0].metadata["collection"] == "shopping_guides"


def test_chunk_rewriter_rejects_structured_response_with_blank_text() -> None:
    """Reject valid JSON responses that do not contain searchable chunk text.

    Falling back to the raw JSON object would persist ``{"text": ""}`` as the
    chunk body and pollute Dense/BM25 indexes. The provider response must fail
    the transform instead.
    """

    source = make_chunk()
    llm = Mock()
    llm.chat.return_value = LLMResponse(
        content='{"text": ""}',
        provider="fake",
        model="fake-rewriter",
    )
    transform = ChunkRewriter(
        llm=llm,
        prompt=config_module.load_prompt("config/prompts/rewrite_chunk_prompt.yaml"),
    )

    with pytest.raises(IngestionError, match="Unable to rewrite chunk"):
        transform.transform([source])


def test_chunk_rewriter_skips_image_placeholder_only_chunk() -> None:
    """Preserve pure image-reference chunks for the Image-to-Text stage.

    Text LLMs may reasonably return an empty rewrite for chunks containing only
    image placeholders. Calling the provider would make the complete document
    fail before optional image captioning can process those references.
    """

    source = make_chunk(
        text=(
            "[[image:image-one]]\n\n"
            "[[image:image-two]]"
        ),
        metadata={"image_refs": ["image-one", "image-two"]},
    )
    llm = Mock()
    transform = ChunkRewriter(
        llm=llm,
        prompt=config_module.load_prompt("config/prompts/rewrite_chunk_prompt.yaml"),
    )

    rewritten = transform.transform([source])

    assert rewritten[0].text == source.text
    assert rewritten[0].id == source.id
    assert rewritten[0].metadata["image_refs"] == ["image-one", "image-two"]
    assert "rewrite" not in rewritten[0].metadata
    llm.chat.assert_not_called()


def test_chunk_rewriter_rewrites_text_segments_and_restores_image_placeholders() -> None:
    """Require rewrite to preserve image nodes without sending them to the LLM."""

    placeholder = "[[image:image-headphones]]"
    source = make_chunk(
        text=f"Original introduction.\n\n{placeholder}\n\nOriginal conclusion.",
        metadata={"image_refs": ["image-headphones"]},
    )
    llm = Mock()
    llm.chat.side_effect = [
        LLMResponse(
            content='{"text": "Improved introduction."}',
            provider="fake",
            model="fake-rewriter",
        ),
        LLMResponse(
            content='{"text": "Improved conclusion."}',
            provider="fake",
            model="fake-rewriter",
        ),
    ]
    transform = ChunkRewriter(
        llm=llm,
        prompt=config_module.load_prompt("config/prompts/rewrite_chunk_prompt.yaml"),
    )

    rewritten = transform.transform([source])

    assert rewritten[0].text == (
        f"Improved introduction.\n\n{placeholder}\n\nImproved conclusion."
    )
    assert rewritten[0].text.count(placeholder) == 1
    assert rewritten[0].metadata["image_refs"] == ["image-headphones"]
    assert llm.chat.call_count == 2
    for call in llm.chat.call_args_list:
        user_message = call.args[0][1].content
        assert placeholder not in user_message


def test_chunk_rewriter_strips_markdown_metadata_sections_from_llm_response() -> None:
    """Require non-JSON provider replies to drop preserved metadata sections."""

    source = make_chunk(metadata={"collection": "shopping_guides"})
    llm = Mock()
    llm.chat.return_value = LLMResponse(
        content=(
            "### Rewritten Chunk\n"
            "Soft silicone stress toys provide quiet tactile feedback.\n\n"
            "### Metadata\n"
            "```json\n"
            '{"collection": "shopping_guides"}\n'
            "```\n\n"
            "### Image References\n"
            "[]"
        ),
        provider="fake",
        model="fake-rewriter",
    )
    transform = ChunkRewriter(
        llm=llm,
        prompt=config_module.load_prompt("config/prompts/rewrite_chunk_prompt.yaml"),
    )

    rewritten = transform.transform([source])

    assert rewritten[0].text == (
        "Soft silicone stress toys provide quiet tactile feedback."
    )
    assert "Metadata" not in rewritten[0].text
    assert "Image References" not in rewritten[0].text


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
    assert merged[0].end_offset == second.end_offset
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
    assert "denoise" not in cleaned[0].metadata
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


def test_image_captioner_replaces_placeholders_and_records_trace_details() -> None:
    """Require image captions to become searchable text, not chunk metadata."""

    source = make_chunk(
        text="[[image:image-stress-ball]]\nSoft silicone models are quiet.",
        metadata={
            "image_refs": ["image-stress-ball"],
        },
    )
    vision_llm = Mock()
    vision_llm.caption_image.return_value = VisionCaptionResponse(
        status="success",
        description="图片展示一个蓝色硅胶解压球，表面为磨砂材质。",
        reason="",
        provider="fake-vision",
        model="fake-vl",
    )

    captioner = ImageCaptioner(
        vision_llm=vision_llm,
        prompt=config_module.load_prompt("config/prompts/image_caption_prompt.yaml"),
        enabled=True,
    )
    context = {
        "document_images": [
            {
                "id": "image-stress-ball",
                "path": "data/images/shopping_guides/image-stress-ball.png",
            }
        ],
        "image_caption_artifacts": {},
    }
    captioned = captioner.caption([source], context=context)

    assert captioned[0] is not source
    assert captioned[0].text == (
        "[[image_caption:image-stress-ball]]\n"
        "图片展示一个蓝色硅胶解压球，表面为磨砂材质。\n\n"
        "Soft silicone models are quiet."
    )
    assert captioned[0].metadata == source.metadata
    assert "image_caption_status" not in captioned[0].metadata
    assert "image_captions" not in captioned[0].metadata
    assert captioner.trace_details() == {
        "provider": "fake-vision",
        "model": "fake-vl",
        "image_count": 1,
        "caption_count": 1,
        "status_counts": {"success": 1},
        "failures": [],
    }
    vision_llm.caption_image.assert_called_once()
    call_kwargs = vision_llm.caption_image.call_args.kwargs
    assert "document_context" not in call_kwargs
    assert context["image_caption_artifacts"] == {
        "image-stress-ball": {
            "image_id": "image-stress-ball",
            "caption": "图片展示一个蓝色硅胶解压球，表面为磨砂材质。",
            "status": "success",
            "provider": "fake-vision",
            "model": "fake-vl",
            "reason": "",
            "source_chunk_ids": ["chunk-1"],
        }
    }


def test_dashscope_vision_llm_sends_local_image_as_base64_data_url(
    tmp_path: Path,
) -> None:
    """Require the provider adapter to encode local images before API calls."""

    image_bytes = b"\x89PNG\r\n\x1a\nfake-image-content"
    image_path = tmp_path / "product.png"
    image_path.write_bytes(image_bytes)
    response = Mock()
    response.choices = [
        Mock(
            message=Mock(
                content=json.dumps(
                    {
                        "status": "success",
                        "description": "A product comparison image.",
                        "reason": "",
                    }
                )
            )
        )
    ]
    client = Mock()
    client.chat.completions.create.return_value = response

    result = DashScopeVisionLLM(model="qwen-vl-max", client=client).caption_image(
        image_path
    )

    messages = client.chat.completions.create.call_args.kwargs["messages"]
    prompt_text = messages[1]["content"][0]["text"]
    image_url = messages[1]["content"][1]["image_url"]["url"]
    assert "Document context" not in prompt_text
    assert "shopping guide" not in prompt_text
    assert image_url == (
        "data:image/png;base64," + base64.b64encode(image_bytes).decode("ascii")
    )
    assert result.description == "A product comparison image."


def test_image_captioner_skips_when_disabled_without_calling_vision_llm() -> None:
    """Require disabled vision captioning to be visible and side-effect free."""

    source = make_chunk(metadata={"image_refs": ["image-1"], "images": []})
    vision_llm = Mock()
    captioner = ImageCaptioner(vision_llm=vision_llm, prompt=None, enabled=False)

    captioned = captioner.caption([source])

    assert captioner.should_caption(source) is False
    assert captioned[0].text == source.text
    assert "image_caption_status" not in captioned[0].metadata
    assert captioner.trace_details()["status_counts"] == {"skipped": 1}
    vision_llm.caption_image.assert_not_called()


def test_image_captioner_ignores_chunks_without_image_refs() -> None:
    """Require text-only chunks to avoid noisy caption metadata."""

    source = make_chunk(metadata={"image_refs": []})
    vision_llm = Mock()

    captioned = ImageCaptioner(vision_llm=vision_llm, prompt=None, enabled=True).caption(
        [source]
    )

    assert "image_caption_status" not in captioned[0].metadata
    assert "image_captions" not in captioned[0].metadata
    assert captioned[0].text == source.text
    vision_llm.caption_image.assert_not_called()


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
    vision_llm.caption_image.return_value = VisionCaptionResponse(
        status="success",
        description="图片展示一个蓝色硅胶解压球。",
        provider="fake-vision",
        model="fake-vl",
    )
    source = make_chunk(
        text="[[image:image-1]]\nSoft silicone models are quiet.",
        metadata={
            "image_refs": ["image-1"],
        },
    )

    pipeline = TransformPipeline.from_settings(
        settings=settings,
        llm=text_llm,
        vision_llm=vision_llm,
    )
    transformed = pipeline.run([source])

    assert "image_caption_status" not in transformed[0].metadata
    assert "[[image:image-1]]" in transformed[0].text
    vision_llm.caption_image.assert_not_called()


def test_image_captioner_records_failed_and_low_quality_results() -> None:
    """Require failed or unusable image understanding to preserve the chunk."""

    failed_chunk = make_chunk(
        chunk_id="chunk-failed",
        text="[[image:image-1]]\nFailed image context.",
        metadata={
            "image_refs": ["image-1"],
        },
    )
    low_quality_chunk = make_chunk(
        chunk_id="chunk-low-quality",
        text="[[image:image-2]]\nLow quality image context.",
        metadata={
            "image_refs": ["image-2"],
        },
    )
    vision_llm = Mock()
    vision_llm.caption_image.side_effect = [
        RuntimeError("vision unavailable"),
        VisionCaptionResponse(
            status="success",
            description="太短",
            reason="",
            provider="fake-vision",
            model="fake-vl",
        ),
    ]

    captioner = ImageCaptioner(vision_llm=vision_llm, prompt=None, enabled=True)
    captioned = captioner.caption(
        [failed_chunk, low_quality_chunk],
        context={
            "document_images": [
                {"id": "image-1", "path": "data/images/shopping_guides/image-1.png"},
                {"id": "image-2", "path": "data/images/shopping_guides/image-2.png"},
            ]
        },
    )

    assert captioned[0].text == failed_chunk.text
    assert captioned[1].text == low_quality_chunk.text
    assert "image_caption_status" not in captioned[0].metadata
    assert "image_captions" not in captioned[1].metadata
    assert captioner.trace_details()["status_counts"] == {
        "failed": 1,
        "low_quality": 1,
    }


def test_image_captioner_reuses_duplicate_image_failure_and_records_safe_cause() -> None:
    """Require one Vision call per image ID and actionable trace-safe failures."""

    chunks = [
        make_chunk(
            chunk_id="chunk-1",
            text="First context.\n[[image:image-shared]]",
            metadata={"image_refs": ["image-shared"]},
        ),
        make_chunk(
            chunk_id="chunk-2",
            text="Second context.\n[[image:image-shared]]",
            chunk_index=1,
            metadata={"image_refs": ["image-shared"]},
        ),
    ]
    vision_llm = Mock()
    vision_llm.caption_image.side_effect = ProviderError(
        "Unable to caption image with DashScope Vision LLM",
        context={"provider": "dashscope", "model": "qwen-vl-max"},
        cause=RuntimeError(
            "HTTP 400 invalid model; api_key=SECRET_KEY; "
            "payload=data:image/png;base64,SECRET_IMAGE"
        ),
    )
    captioner = ImageCaptioner(vision_llm=vision_llm, prompt=None, enabled=True)

    output = captioner.caption(
        chunks,
        context={
            "document_images": [
                {
                    "id": "image-shared",
                    "path": "data/images/shopping_guides/image-shared.png",
                }
            ]
        },
    )

    assert [chunk.text for chunk in output] == [chunk.text for chunk in chunks]
    vision_llm.caption_image.assert_called_once()
    assert captioner.trace_details() == {
        "provider": "dashscope",
        "model": "qwen-vl-max",
        "image_count": 1,
        "caption_count": 0,
        "status_counts": {"failed": 1},
        "failures": [
            {
                "image_id": "image-shared",
                "status": "failed",
                "reason": (
                    "Unable to caption image with DashScope Vision LLM: "
                    "RuntimeError: HTTP 400 invalid model; "
                    "api_key=[redacted-secret]; "
                    "payload=[redacted-base64-image]"
                ),
                "error_type": "RuntimeError",
            }
        ],
    }
