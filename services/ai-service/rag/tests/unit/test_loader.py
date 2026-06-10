"""Protect document deduplication before any source loader is invoked.

C1 establishes the first executable boundary of ``IngestionPipeline``. These
tests verify that hashing uses original file bytes, successful unchanged
sources stop before Loader work, force mode bypasses deduplication, and skipped
runs persist a complete ingestion trace summary.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

RAG_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RAG_ROOT))

errors_module = importlib.import_module("src.core.errors")
config_module = importlib.import_module("src.core.config")
types_module = importlib.import_module("src.core.types")
llm_module = importlib.import_module("src.libs.llm")
pipeline_module = importlib.import_module("src.ingestion.pipeline")
pdf_conversion_module = importlib.import_module("src.ingestion.pdf_to_markdown")
markdown_loader_module = importlib.import_module("src.libs.loader.markdown_loader")
pdf_loader_module = importlib.import_module("src.libs.loader.pdf_loader")

Document = types_module.Document
IngestionError = errors_module.IngestionError
LLMResponse = llm_module.LLMResponse
SETTINGS_PATH = RAG_ROOT / "config" / "settings.example.yaml"
load_settings = config_module.load_settings
IngestionPipeline = pipeline_module.IngestionPipeline
calculate_sha256 = pipeline_module.calculate_sha256
should_skip_document = pipeline_module.should_skip_document
ExtractedImage = pdf_conversion_module.ExtractedImage
MarkItDownConverter = pdf_conversion_module.MarkItDownConverter
PdfConversionResult = pdf_conversion_module.PdfConversionResult
extract_images = pdf_conversion_module.extract_images
MarkdownLoader = markdown_loader_module.MarkdownLoader
PdfLoader = pdf_loader_module.PdfLoader


def test_ingest_parse_args_supports_path_collection_and_force() -> None:
    """Require the C11 CLI to expose every approved ingestion option."""

    ingest_module = importlib.import_module("src.scripts.ingest")

    args = ingest_module.parse_args(
        [
            "--path",
            "data/raw/shopping_guides",
            "--collection",
            "shopping_guides",
            "--force",
        ]
    )

    assert args.path == Path("data/raw/shopping_guides")
    assert args.collection == "shopping_guides"
    assert args.force is True


def test_ingest_parse_args_reports_missing_path(capsys: pytest.CaptureFixture[str]) -> None:
    """Require argparse to return a readable error when --path is missing."""

    ingest_module = importlib.import_module("src.scripts.ingest")

    with pytest.raises(SystemExit) as captured:
        ingest_module.parse_args([])

    assert captured.value.code == 2
    assert "--path" in capsys.readouterr().err


def test_run_ingest_cli_uses_default_collection_and_forwards_force(
    tmp_path: Path,
) -> None:
    """Require one source file to run through the injected Pipeline boundary."""

    ingest_module = importlib.import_module("src.scripts.ingest")
    source = tmp_path / "guide.md"
    source.write_text("# Guide", encoding="utf-8")
    settings = SimpleNamespace(
        project=SimpleNamespace(default_collection="shopping_guides"),
        database=SimpleNamespace(),
    )
    pool = Mock()
    pipeline = Mock()
    pipeline.run.return_value = SimpleNamespace(
        trace_id="trace-c11",
        status="indexed",
        source_uri=str(source.resolve()),
        source_hash="a" * 64,
        trace_summary={"chunk_count": 1},
    )
    built_sources: list[Path] = []
    output: list[str] = []

    exit_code = ingest_module.run_ingest_cli(
        ["--path", str(source), "--force"],
        settings_loader=lambda: settings,
        pool_factory=lambda _: pool,
        schema_initializer=lambda active_pool: active_pool,
        pipeline_builder=lambda path, _settings, _pool: (
            built_sources.append(path) or pipeline
        ),
        output=output.append,
        error_output=Mock(),
    )

    assert exit_code == 0
    assert built_sources == [source.resolve()]
    pipeline.run.assert_called_once_with(
        source.resolve(),
        collection_id="shopping_guides",
        force=True,
    )
    pool.open.assert_called_once_with()
    pool.close.assert_called_once_with()
    assert json.loads(output[0]) == {
        "collection": "shopping_guides",
        "force": True,
        "processed": 1,
        "results": [
            {
                "source": str(source.resolve()),
                "status": "indexed",
                "trace_id": "trace-c11",
                "source_hash": "a" * 64,
                "summary": {"chunk_count": 1},
            }
        ],
    }


def test_run_ingest_cli_discovers_supported_directory_sources_in_order(
    tmp_path: Path,
) -> None:
    """Require directory ingestion to recurse, filter unsupported files, and sort."""

    ingest_module = importlib.import_module("src.scripts.ingest")
    nested = tmp_path / "nested"
    nested.mkdir()
    markdown = nested / "b.md"
    pdf = tmp_path / "a.pdf"
    ignored = tmp_path / "notes.txt"
    markdown.write_text("# B", encoding="utf-8")
    pdf.write_bytes(b"%PDF fixture")
    ignored.write_text("ignore", encoding="utf-8")
    settings = SimpleNamespace(
        project=SimpleNamespace(default_collection="default"),
        database=SimpleNamespace(),
    )
    pool = Mock()
    processed: list[Path] = []

    def build_pipeline(source: Path, _settings: object, _pool: object) -> Mock:
        """Create a source-specific Pipeline double and record source ordering."""

        processed.append(source)
        pipeline = Mock()
        pipeline.run.return_value = SimpleNamespace(
            trace_id=f"trace-{source.stem}",
            status="indexed",
            source_uri=str(source),
            source_hash="b" * 64,
            trace_summary={},
        )
        return pipeline

    exit_code = ingest_module.run_ingest_cli(
        ["--path", str(tmp_path), "--collection", "catalog"],
        settings_loader=lambda: settings,
        pool_factory=lambda _: pool,
        schema_initializer=lambda active_pool: active_pool,
        pipeline_builder=build_pipeline,
        output=Mock(),
        error_output=Mock(),
    )

    assert exit_code == 0
    assert processed == sorted([pdf.resolve(), markdown.resolve()])


def test_run_ingest_cli_reports_unsupported_or_empty_source_path(
    tmp_path: Path,
) -> None:
    """Require invalid source selections to fail before opening PostgreSQL."""

    ingest_module = importlib.import_module("src.scripts.ingest")
    source = tmp_path / "notes.txt"
    source.write_text("unsupported", encoding="utf-8")
    settings_loader = Mock()
    pool_factory = Mock()
    errors: list[str] = []

    exit_code = ingest_module.run_ingest_cli(
        ["--path", str(source)],
        settings_loader=settings_loader,
        pool_factory=pool_factory,
        output=Mock(),
        error_output=errors.append,
    )

    assert exit_code == 2
    assert "No supported ingestion files" in errors[0]
    settings_loader.assert_not_called()
    pool_factory.assert_not_called()


def test_run_ingest_cli_closes_pool_and_reports_pipeline_failure(
    tmp_path: Path,
) -> None:
    """Require runtime failures to return code 1 without leaking the DB pool."""

    ingest_module = importlib.import_module("src.scripts.ingest")
    source = tmp_path / "guide.md"
    source.write_text("# Guide", encoding="utf-8")
    settings = SimpleNamespace(
        project=SimpleNamespace(default_collection="shopping_guides"),
        database=SimpleNamespace(),
    )
    pool = Mock()
    pipeline = Mock()
    pipeline.run.side_effect = RuntimeError("embedding provider unavailable")
    errors: list[str] = []

    exit_code = ingest_module.run_ingest_cli(
        ["--path", str(source)],
        settings_loader=lambda: settings,
        pool_factory=lambda _: pool,
        schema_initializer=lambda active_pool: active_pool,
        pipeline_builder=lambda _source, _settings, _pool: pipeline,
        output=Mock(),
        error_output=errors.append,
    )

    assert exit_code == 1
    assert errors == ["Ingestion failed: embedding provider unavailable"]
    pool.close.assert_called_once_with()


def test_run_ingest_cli_loads_environment_from_nearest_parent_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require local CLI runs to discover a parent .env before settings validation."""

    ingest_module = importlib.import_module("src.scripts.ingest")
    project_root = tmp_path / "project"
    working_directory = project_root / "services" / "rag"
    working_directory.mkdir(parents=True)
    source = working_directory / "guide.md"
    source.write_text("# Guide", encoding="utf-8")
    (project_root / ".env").write_text(
        "DATABASE_URL=postgresql://local:test@localhost:5432/rag\n"
        "DASHSCOPE_API_KEY=local-test-key\n"
        "DASHSCOPE_BASE_URL=https://dashscope.example.test/v1\n",
        encoding="utf-8",
    )
    for variable in ("DATABASE_URL", "DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.chdir(working_directory)
    observed_environment: dict[str, str | None] = {}
    settings = SimpleNamespace(
        project=SimpleNamespace(default_collection="shopping_guides"),
        database=SimpleNamespace(),
    )
    pool = Mock()
    pipeline = Mock()
    pipeline.run.return_value = SimpleNamespace(
        trace_id="trace-dotenv",
        status="indexed",
        source_uri=str(source.resolve()),
        source_hash="c" * 64,
        trace_summary={},
    )

    def load_settings_after_dotenv() -> object:
        """Capture the process environment visible to settings validation."""

        for variable in ("DATABASE_URL", "DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL"):
            observed_environment[variable] = os.getenv(variable)
        return settings

    exit_code = ingest_module.run_ingest_cli(
        ["--path", str(source)],
        settings_loader=load_settings_after_dotenv,
        pool_factory=lambda _: pool,
        schema_initializer=lambda active_pool: active_pool,
        pipeline_builder=lambda _source, _settings, _pool: pipeline,
        output=Mock(),
        error_output=Mock(),
    )

    assert exit_code == 0
    assert observed_environment == {
        "DATABASE_URL": "postgresql://local:test@localhost:5432/rag",
        "DASHSCOPE_API_KEY": "local-test-key",
        "DASHSCOPE_BASE_URL": "https://dashscope.example.test/v1",
    }


def test_run_ingest_cli_does_not_override_injected_environment_with_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require container or shell environment values to override local .env defaults."""

    ingest_module = importlib.import_module("src.scripts.ingest")
    source = tmp_path / "guide.md"
    source.write_text("# Guide", encoding="utf-8")
    (tmp_path / ".env").write_text("DATABASE_URL=postgresql://dotenv/value\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DATABASE_URL", "postgresql://injected/value")
    settings = SimpleNamespace(
        project=SimpleNamespace(default_collection="shopping_guides"),
        database=SimpleNamespace(),
    )
    observed_database_url: list[str | None] = []
    pool = Mock()
    pipeline = Mock()
    pipeline.run.return_value = SimpleNamespace(
        trace_id="trace-injected-env",
        status="indexed",
        source_uri=str(source.resolve()),
        source_hash="d" * 64,
        trace_summary={},
    )

    def load_settings_after_dotenv() -> object:
        """Capture the effective database URL without exposing it in CLI output."""

        observed_database_url.append(os.getenv("DATABASE_URL"))
        return settings

    exit_code = ingest_module.run_ingest_cli(
        ["--path", str(source)],
        settings_loader=load_settings_after_dotenv,
        pool_factory=lambda _: pool,
        schema_initializer=lambda active_pool: active_pool,
        pipeline_builder=lambda _source, _settings, _pool: pipeline,
        output=Mock(),
        error_output=Mock(),
    )

    assert exit_code == 0
    assert observed_database_url == ["postgresql://injected/value"]


def test_ingest_runtime_paths_resolve_relative_to_rag_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require configured data paths to remain stable across CLI working directories."""

    ingest_module = importlib.import_module("src.scripts.ingest")
    monkeypatch.chdir(tmp_path)
    absolute_path = tmp_path / "absolute-images"

    assert ingest_module._resolve_runtime_path("data/images") == (
        RAG_ROOT / "data" / "images"
    ).resolve()
    assert ingest_module._resolve_runtime_path(absolute_path) == absolute_path.resolve()


def test_ingest_builds_document_summarizer_from_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require CLI composition to enable the independent summary step."""

    ingest_module = importlib.import_module("src.scripts.ingest")
    config_module = importlib.import_module("src.core.config")
    llm_module = importlib.import_module("src.libs.llm")
    settings = config_module.load_settings(
        RAG_ROOT / "config" / "settings.example.yaml",
        environ={},
        validate_environment=False,
    )
    fake_llm = Mock()
    fake_llm.chat.return_value = llm_module.LLMResponse(
        content="A generated document summary.",
        provider="fake",
        model="fake-summary",
    )
    captured: dict[str, object] = {}

    def fake_create(**kwargs: object) -> Mock:
        captured.update(kwargs)
        return fake_llm

    monkeypatch.setattr(ingest_module.LLMFactory, "create", fake_create)

    summarizer = ingest_module._build_document_summarizer(settings)

    assert type(summarizer).__name__ == "DocumentSummarizer"
    assert captured["settings"] is settings
    assert captured["provider"] == "deepseek"


def test_calculate_sha256_hashes_original_file_bytes(tmp_path: Path) -> None:
    """Require byte-stable hashing before parsing or Markdown conversion."""

    source = tmp_path / "guide.md"
    content = b"# Guide\r\n\r\nOriginal source bytes.\x00"
    source.write_bytes(content)

    digest = calculate_sha256(source, block_size=7)

    assert digest == sha256(content).hexdigest()


def test_calculate_sha256_wraps_unreadable_sources() -> None:
    """Require filesystem failures to use the ingestion error boundary."""

    missing = Path("missing-c1-source.md")

    with pytest.raises(IngestionError) as captured:
        calculate_sha256(missing)

    assert captured.value.context["operation"] == "source_hash"
    assert captured.value.context["source"].endswith("missing-c1-source.md")


def test_should_skip_document_uses_repository_and_force_bypasses_lookup() -> None:
    """Require force mode to continue without consulting persisted success state."""

    documents = Mock()
    documents.has_successful_source_hash.return_value = True

    assert (
        should_skip_document(
            documents,
            collection_id="shopping_guides",
            source_path="D:/data/guide.md",
            source_hash="a" * 64,
        )
        is True
    )
    assert (
        should_skip_document(
            documents,
            collection_id="shopping_guides",
            source_path="D:/data/guide.md",
            source_hash="a" * 64,
            force=True,
        )
        is False
    )
    documents.has_successful_source_hash.assert_called_once_with(
        collection_id="shopping_guides",
        source_path="D:/data/guide.md",
        source_hash="a" * 64,
    )


def test_ingestion_pipeline_skips_before_loader_and_persists_trace(
    tmp_path: Path,
) -> None:
    """Require unchanged successful sources to stop before expensive stages."""

    source = tmp_path / "guide.md"
    source.write_text("# Existing guide", encoding="utf-8")
    loader = Mock()
    documents = Mock()
    documents.has_successful_source_hash.return_value = True
    traces = Mock()
    traces.upsert_ingestion_trace.side_effect = lambda trace: trace
    pipeline = IngestionPipeline(
        loader=loader,
        document_repository=documents,
        trace_repository=traces,
        trace_id_factory=lambda: "trace-c1-skip",
    )

    result = pipeline.run(source, collection_id="shopping_guides")

    assert result.status == "skipped"
    assert result.document is None
    assert result.trace_id == "trace-c1-skip"
    assert result.trace_summary["skip_reason"] == "successful_source_hash_match"
    loader.load.assert_not_called()
    stored_trace = traces.upsert_ingestion_trace.call_args.args[0]
    assert stored_trace.status == "skipped"
    assert stored_trace.source_uri == str(source.resolve())
    assert stored_trace.source_hash == calculate_sha256(source)
    assert stored_trace.basic_info["trace_type"] == "ingestion"
    assert stored_trace.stages[0]["stage"] == "dedup"
    assert stored_trace.stages[0]["matched"] is True
    assert stored_trace.summary_metrics["skipped"] is True
    assert stored_trace.finished_at is not None


def test_ingestion_pipeline_calls_loader_when_source_changed_or_forced(
    tmp_path: Path,
) -> None:
    """Require changed and force-requested sources to continue into Loader."""

    source = tmp_path / "new-guide.md"
    source.write_text("# New guide", encoding="utf-8")
    loaded_document = Document(
        id="doc-new",
        text="# New guide",
        metadata={"source_path": str(source.resolve())},
    )
    loader = Mock()
    loader.load.return_value = loaded_document
    documents = Mock()
    documents.has_successful_source_hash.return_value = False
    traces = Mock()
    pipeline = IngestionPipeline(
        loader=loader,
        document_repository=documents,
        trace_repository=traces,
        trace_id_factory=lambda: "trace-c1-load",
    )

    changed_result = pipeline.run(source, collection_id="shopping_guides")
    forced_result = pipeline.run(
        source,
        collection_id="shopping_guides",
        force=True,
    )

    assert changed_result.status == "loaded"
    assert changed_result.document == loaded_document
    assert forced_result.status == "loaded"
    assert forced_result.document == loaded_document
    assert loader.load.call_count == 2
    loader.load.assert_called_with(source.resolve())
    documents.has_successful_source_hash.assert_called_once()
    traces.upsert_ingestion_trace.assert_not_called()


def test_ingestion_pipeline_summarizes_loaded_document_when_configured(
    tmp_path: Path,
) -> None:
    """Require the independent summary step to run after Loader succeeds."""

    source = tmp_path / "summary-guide.md"
    source.write_text("# Summary guide", encoding="utf-8")
    loaded_document = Document(
        id="doc-summary",
        text="# Summary guide",
        metadata={"source_path": str(source.resolve())},
    )
    summarized_document = loaded_document.model_copy(
        update={"summary": "A short guide summary."},
        deep=True,
    )
    loader = Mock()
    loader.load.return_value = loaded_document
    summarizer = Mock()
    summarizer.summarize.return_value = summarized_document
    documents = Mock()
    documents.has_successful_source_hash.return_value = False
    traces = Mock()
    pipeline = IngestionPipeline(
        loader=loader,
        document_repository=documents,
        trace_repository=traces,
        document_summarizer=summarizer,
        trace_id_factory=lambda: "trace-c2-summary",
    )

    result = pipeline.run(source, collection_id="shopping_guides")

    assert result.status == "loaded"
    assert result.document == summarized_document
    summarizer.summarize.assert_called_once_with(
        loaded_document,
        context={
            "collection": "shopping_guides",
            "source_uri": str(source.resolve()),
            "source_hash": calculate_sha256(source),
        },
    )


def test_ingest_builder_enables_document_summarizer_when_config_is_absent() -> None:
    """Require stale local settings to still enable the default summary step."""

    ingest_module = importlib.import_module("src.scripts.ingest")
    settings = load_settings(SETTINGS_PATH, validate_environment=False)
    settings.ingestion.__pydantic_extra__.pop("document_summary", None)
    fake_llm = Mock()
    fake_llm.chat.return_value = LLMResponse(
        content="A short document summary.",
        provider="fake",
        model="fake",
    )

    summarizer = ingest_module._build_document_summarizer(
        settings,
        llm=fake_llm,
    )

    assert summarizer is not None
    document = Document(id="doc-1", text="# Guide", metadata={})
    assert summarizer.summarize(document).summary == "A short document summary."


def test_ingest_builder_uses_configured_document_summary_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require document summaries to use the configured LLM provider.

    The summary step can intentionally use a provider that is different from
    other LLM-backed stages. This test protects the configuration contract so
    ``ingestion.document_summary.llm_provider`` is passed to ``LLMFactory``.
    """

    ingest_module = importlib.import_module("src.scripts.ingest")
    settings = load_settings(SETTINGS_PATH, validate_environment=False)
    captured: dict[str, object] = {}
    fake_llm = Mock()
    fake_llm.chat.return_value = LLMResponse(
        content="A DeepSeek generated document summary.",
        provider="deepseek",
        model="deepseek-v4-flash",
    )

    def fake_create(**kwargs: object) -> Mock:
        captured.update(kwargs)
        return fake_llm

    monkeypatch.setattr(ingest_module.LLMFactory, "create", fake_create)

    summarizer = ingest_module._build_document_summarizer(settings)

    assert summarizer is not None
    assert captured["settings"] is settings
    assert captured["provider"] == "deepseek"
    document = Document(id="doc-1", text="# Guide", metadata={})
    assert summarizer.summarize(document).summary == (
        "A DeepSeek generated document summary."
    )


def test_markdown_loader_normalizes_text_and_extracts_heading_hierarchy(
    tmp_path: Path,
) -> None:
    """Require canonical Markdown and ordered heading paths in loader metadata."""

    source = tmp_path / "audio-guide.md"
    original = (
        b"\xef\xbb\xbf# Audio Guide  \r\n\r\n"
        b"Choose by use case.   \r\n\r\n\r\n"
        b"## Wireless\r\n\r\nBattery matters.\r\n"
        b"### Noise Control\r\n"
    )
    source.write_bytes(original)

    document = MarkdownLoader().load(source)

    expected_text = (
        "# Audio Guide\n\n"
        "Choose by use case.\n\n"
        "## Wireless\n\n"
        "Battery matters.\n"
        "### Noise Control\n"
    )
    assert document.text == expected_text
    assert document.metadata["title"] == "Audio Guide"
    assert document.metadata["source_hash"] == sha256(original).hexdigest()
    assert document.metadata["headings"] == [
        {
            "level": 1,
            "title": "Audio Guide",
            "path": ["Audio Guide"],
            "text_offset": expected_text.index("# Audio Guide"),
        },
        {
            "level": 2,
            "title": "Wireless",
            "path": ["Audio Guide", "Wireless"],
            "text_offset": expected_text.index("## Wireless"),
        },
        {
            "level": 3,
            "title": "Noise Control",
            "path": ["Audio Guide", "Wireless", "Noise Control"],
            "text_offset": expected_text.index("### Noise Control"),
        },
    ]
    assert "images" not in document.metadata
    assert MarkdownLoader().load(source).id == document.id


def test_markdown_loader_replaces_local_images_with_stable_placeholders(
    tmp_path: Path,
) -> None:
    """Require local Markdown image syntax to become offset-addressable metadata."""

    image = tmp_path / "diagram.png"
    image.write_bytes(b"fake-png-content")
    source = tmp_path / "visual-guide.md"
    source.write_text(
        "# Visual Guide\n\nBefore.\n\n![Signal flow](diagram.png)\n\nAfter.\n",
        encoding="utf-8",
    )

    document = MarkdownLoader().load(source)

    images = document.metadata["images"]
    assert len(images) == 1
    image_metadata = images[0]
    placeholder = f"[[image:{image_metadata['id']}]]"
    assert placeholder in document.text
    assert "![Signal flow](diagram.png)" not in document.text
    assert image_metadata["path"] == str(image.resolve())
    assert image_metadata["page"] is None
    assert image_metadata["text_offset"] == document.text.index(placeholder)
    assert image_metadata["text_length"] == len(placeholder)
    assert image_metadata["position"] == {
        "source_type": "markdown",
        "line": 5,
        "alt_text": "Signal flow",
    }


def test_markdown_loader_does_not_read_images_outside_source_directory(
    tmp_path: Path,
) -> None:
    """Require Markdown image resolution to reject parent-directory traversal."""

    source_directory = tmp_path / "documents"
    source_directory.mkdir()
    outside_image = tmp_path / "secret.png"
    outside_image.write_bytes(b"sensitive-content")
    source = source_directory / "unsafe.md"
    original_image_syntax = "![Unsafe](../secret.png)"
    source.write_text(
        f"# Unsafe Guide\n\n{original_image_syntax}\n",
        encoding="utf-8",
    )

    document = MarkdownLoader().load(source)

    assert original_image_syntax in document.text
    assert "images" not in document.metadata


def test_markdown_loader_ignores_heading_syntax_inside_fenced_code(
    tmp_path: Path,
) -> None:
    """Require heading metadata to represent document structure, not code text."""

    source = tmp_path / "code-sample.md"
    source.write_text(
        "```markdown\n# Not A Document Heading\n```\n\n# Real Heading\n",
        encoding="utf-8",
    )

    document = MarkdownLoader().load(source)

    assert document.metadata["title"] == "Real Heading"
    assert document.metadata["headings"] == [
        {
            "level": 1,
            "title": "Real Heading",
            "path": ["Real Heading"],
            "text_offset": document.text.index("# Real Heading"),
        }
    ]


def test_markdown_loader_does_not_replace_image_examples_inside_fenced_code(
    tmp_path: Path,
) -> None:
    """Require code examples containing Markdown image syntax to remain intact."""

    image = tmp_path / "diagram.png"
    image.write_bytes(b"real-image")
    source = tmp_path / "image-example.md"
    image_syntax = "![Example](diagram.png)"
    source.write_text(
        f"# Syntax Guide\n\n```markdown\n{image_syntax}\n```\n",
        encoding="utf-8",
    )

    document = MarkdownLoader().load(source)

    assert image_syntax in document.text
    assert "images" not in document.metadata


def test_markitdown_converter_normalizes_text_and_delegates_image_extraction(
    tmp_path: Path,
) -> None:
    """Require PDF text and image parsing to remain separate injectable concerns."""

    source = tmp_path / "guide.pdf"
    source.write_bytes(b"%PDF-test")
    markitdown = Mock()
    markitdown.convert.return_value = SimpleNamespace(
        text_content="\ufeff# PDF Guide  \r\n\r\nBody.\r\n"
    )
    extracted = (
        ExtractedImage(
            content=b"image-bytes",
            suffix=".png",
            page=2,
            position={"x": 10.0, "y": 20.0, "width": 30.0, "height": 40.0},
        ),
    )
    image_extractor = Mock(return_value=extracted)
    converter = MarkItDownConverter(
        converter=markitdown,
        image_extractor=image_extractor,
    )

    result = converter.convert(source)

    assert result.markdown == "# PDF Guide\n\nBody.\n"
    assert result.images == extracted
    markitdown.convert.assert_called_once_with(str(source.resolve()))
    image_extractor.assert_called_once_with(source.resolve())


def test_extract_images_reads_page_position_and_original_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require the PyMuPDF adapter to preserve image bytes and source geometry."""

    source = tmp_path / "with-image.pdf"
    source.write_bytes(b"%PDF-image")

    class FakeRect:
        """Expose the coordinate attributes returned by PyMuPDF rectangles."""

        x0 = 1.0
        y0 = 2.0
        x1 = 21.0
        y1 = 32.0

    class FakePage:
        """Return one embedded image and its first physical occurrence."""

        def get_images(self, *, full: bool) -> list[tuple[int]]:
            assert full is True
            return [(7,), (7,)]

        def get_image_rects(self, xref: int) -> list[FakeRect]:
            assert xref == 7
            return [FakeRect()]

    class FakeDocument:
        """Provide the subset of the PyMuPDF document API used by extraction."""

        def __enter__(self) -> FakeDocument:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def __iter__(self) -> object:
            return iter([FakePage()])

        def extract_image(self, xref: int) -> dict[str, object]:
            assert xref == 7
            return {
                "image": b"raw-image",
                "ext": "jpeg",
                "width": 20,
                "height": 30,
            }

    fake_fitz = SimpleNamespace(open=lambda path: FakeDocument())
    monkeypatch.setitem(sys.modules, "fitz", fake_fitz)

    images = extract_images(source)

    assert images == (
        ExtractedImage(
            content=b"raw-image",
            suffix=".jpeg",
            page=1,
            position={
                "x": 1.0,
                "y": 2.0,
                "width": 20.0,
                "height": 30.0,
                "bbox": [1.0, 2.0, 21.0, 32.0],
                "pixel_width": 20,
                "pixel_height": 30,
                "sequence": 0,
            },
        ),
    )


def test_pdf_loader_persists_images_and_injects_valid_metadata(
    tmp_path: Path,
) -> None:
    """Require PDF images to produce stable placeholders, files, and metadata."""

    source = tmp_path / "shopping.pdf"
    source.write_bytes(b"%PDF-shopping-guide")
    conversion = PdfConversionResult(
        markdown="# Shopping Guide\n\nCompare materials.\n",
        images=(
            ExtractedImage(
                content=b"first-image",
                suffix=".png",
                page=1,
                position={"x": 1.0, "y": 2.0, "width": 50.0, "height": 60.0},
            ),
        ),
    )
    converter = Mock()
    converter.convert.return_value = conversion
    loader = PdfLoader(
        converter=converter,
        image_output_dir=tmp_path / "images",
    )

    document = loader.load(source)
    repeated = loader.load(source)

    assert document.id == repeated.id
    assert document.metadata["title"] == "Shopping Guide"
    assert document.metadata["source_type"] == "pdf"
    assert document.metadata["source_hash"] == sha256(source.read_bytes()).hexdigest()
    assert document.metadata["headings"] == [
        {
            "level": 1,
            "title": "Shopping Guide",
            "path": ["Shopping Guide"],
            "text_offset": 0,
        }
    ]
    image_metadata = document.metadata["images"][0]
    placeholder = f"[[image:{image_metadata['id']}]]"
    assert placeholder in document.text
    assert image_metadata["text_offset"] == document.text.index(placeholder)
    assert image_metadata["text_length"] == len(placeholder)
    assert Path(image_metadata["path"]).read_bytes() == b"first-image"
    assert image_metadata["page"] == 1
    assert image_metadata["position"]["width"] == 50.0


def test_pdf_loader_inserts_image_placeholders_near_source_page_text(
    tmp_path: Path,
) -> None:
    """Require PDF placeholders to follow page/y order instead of appending."""

    source = tmp_path / "positioned-images.pdf"
    source.write_bytes(b"%PDF-positioned-images")
    conversion = PdfConversionResult(
        markdown=(
            "# Shopping Guide\n\n"
            "Page one introduction.\n\n"
            "<!-- page: 2 -->\n\n"
            "Page two comparison table.\n"
        ),
        images=(
            ExtractedImage(
                content=b"page-two-image",
                suffix=".png",
                page=2,
                position={"x": 12.0, "y": 30.0, "width": 80.0, "height": 40.0},
            ),
            ExtractedImage(
                content=b"page-one-image",
                suffix=".png",
                page=1,
                position={"x": 10.0, "y": 20.0, "width": 50.0, "height": 40.0},
            ),
        ),
    )
    converter = Mock()
    converter.convert.return_value = conversion

    document = PdfLoader(
        converter=converter,
        image_output_dir=tmp_path / "images",
    ).load(source)

    first_image, second_image = document.metadata["images"]
    first_placeholder = f"[[image:{first_image['id']}]]"
    second_placeholder = f"[[image:{second_image['id']}]]"
    page_two_marker = "<!-- page: 2 -->"

    assert first_image["page"] == 1
    assert second_image["page"] == 2
    assert document.text.index("Page one introduction.") < document.text.index(
        first_placeholder
    )
    assert document.text.index(first_placeholder) < document.text.index(
        page_two_marker
    )
    assert document.text.index("Page two comparison table.") < document.text.index(
        second_placeholder
    )
    assert document.text.index(second_placeholder) < len(document.text.rstrip())
    assert first_image["text_offset"] == document.text.index(first_placeholder)
    assert second_image["text_offset"] == document.text.index(second_placeholder)


def test_pdf_loader_omits_images_metadata_when_pdf_has_no_images(
    tmp_path: Path,
) -> None:
    """Require text-only PDFs to avoid empty or misleading image metadata."""

    source = tmp_path / "text-only.pdf"
    source.write_bytes(b"%PDF-text-only")
    converter = Mock()
    converter.convert.return_value = PdfConversionResult(
        markdown="# Text Only\n\nNo embedded images.\n",
        images=(),
    )

    document = PdfLoader(
        converter=converter,
        image_output_dir=tmp_path / "images",
    ).load(source)

    assert document.text == "# Text Only\n\nNo embedded images.\n"
    assert "images" not in document.metadata
    assert not (tmp_path / "images").exists()


def test_pdf_loader_removes_partial_image_files_when_persistence_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require a failed multi-image write to leave no orphaned source assets."""

    source = tmp_path / "partial-write.pdf"
    source.write_bytes(b"%PDF-partial-write")
    conversion = PdfConversionResult(
        markdown="# Partial Write\n",
        images=(
            ExtractedImage(
                content=b"first-image",
                suffix=".png",
                page=1,
                position={"width": 10, "height": 10},
            ),
            ExtractedImage(
                content=b"second-image",
                suffix=".png",
                page=2,
                position={"width": 20, "height": 20},
            ),
        ),
    )
    converter = Mock()
    converter.convert.return_value = conversion
    original_replace = Path.replace
    replace_calls = 0

    def fail_second_replace(path: Path, target: Path) -> Path:
        """Raise on the second atomic rename after one image was persisted."""

        nonlocal replace_calls
        replace_calls += 1
        if replace_calls == 2:
            raise OSError("simulated image persistence failure")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_second_replace)
    image_root = tmp_path / "images"

    with pytest.raises(IngestionError):
        PdfLoader(
            converter=converter,
            image_output_dir=image_root,
        ).load(source)

    assert not list(image_root.rglob("*.*"))


def test_pdf_loader_wraps_conversion_failures_with_source_context(
    tmp_path: Path,
) -> None:
    """Require converter failures to cross the loader boundary as IngestionError."""

    source = tmp_path / "broken.pdf"
    source.write_bytes(b"%PDF-broken")
    converter = Mock()
    converter.convert.side_effect = RuntimeError("parser failed")

    with pytest.raises(IngestionError) as captured:
        PdfLoader(converter=converter).load(source)

    assert captured.value.context == {
        "operation": "pdf_load",
        "source": str(source.resolve()),
    }
    assert isinstance(captured.value.cause, RuntimeError)
