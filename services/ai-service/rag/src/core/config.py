"""Load and validate runtime settings and versioned Prompt definitions.

This module is the configuration boundary between checked-in YAML documents and
the rest of the RAG application. It converts untrusted YAML mappings into typed
Pydantic models, verifies that configured selectors reference real providers,
and reports all missing environment variables required by active components
before factories or pipelines are created.

The loader resolves repository-relative paths for local development while also
accepting absolute paths for containers and tests. It does not instantiate
providers, open database connections, render Prompts, or read secret values
from the YAML files themselves.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from string import Formatter
from typing import Any, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

RAG_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SETTINGS_PATH = RAG_ROOT / "config" / "settings.yaml"


class ConfigSection(BaseModel):
    """Provide a shared model policy for forward-compatible config sections.

    Known fields remain typed and validated, while ``extra="allow"`` lets later
    tasks add provider-specific options without forcing A5 to predict every SDK
    parameter. Factories still receive validated section objects instead of raw
    top-level dictionaries.
    """

    model_config = ConfigDict(extra="allow")


class ProjectSettings(ConfigSection):
    """Describe the RAG service identity and default collection."""

    name: str = Field(min_length=1)
    default_collection: str = Field(min_length=1)
    environment: str = Field(min_length=1)


class DatabaseSettings(ConfigSection):
    """Describe the database backend and environment-based connection source."""

    provider: str = Field(min_length=1)
    url_env: str = Field(min_length=1)
    pool_size: int = Field(gt=0)
    timezone: str = Field(default="Asia/Shanghai", min_length=1)
    echo_sql: bool = False


class ProviderSettings(ConfigSection):
    """Represent common options shared by model and embedding providers.

    Provider implementations may add SDK-specific fields through the inherited
    ``extra="allow"`` policy. Environment fields contain variable names only;
    secret values are supplied separately at runtime.
    """

    model: str | None = None
    api_key: str | None = None
    api_key_env: str | None = None
    base_url_env: str | None = None
    base_url: str | None = None
    timeout_seconds: int | None = Field(default=None, gt=0)
    dimensions: int | None = Field(default=None, gt=0)

    def environment_references(self) -> set[str]:
        """Return environment-variable names required by this provider.

        Returns:
            A deduplicated set containing configured API-key and base-URL
            variable names. Literal ``base_url`` values are not included because
            they require no environment lookup.
        """

        return {reference for reference in (self.api_key_env, self.base_url_env) if reference}


class ProviderGroupSettings(ConfigSection):
    """Select one implementation from a named provider registry."""

    default: str = Field(min_length=1)
    fallback: str | None = None
    providers: dict[str, ProviderSettings] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_provider_selectors(self) -> Self:
        """Ensure selectors exist and the selected provider names a model.

        Returns:
            The validated provider group.

        Raises:
            ValueError: If ``default`` or a non-sentinel ``fallback`` references
                a provider absent from the configured registry, or if the
                selected provider omits its model identifier.
        """

        if self.default not in self.providers:
            raise ValueError(
                f"Selected provider '{self.default}' is not defined; "
                f"available providers: {sorted(self.providers)}"
            )
        if not self.selected_provider.model:
            raise ValueError(f"Selected provider '{self.default}' must define a model")
        if self.fallback not in (None, "none") and self.fallback not in self.providers:
            raise ValueError(
                f"Fallback provider '{self.fallback}' is not defined; "
                f"available providers: {sorted(self.providers)}"
            )
        return self

    @property
    def selected_provider(self) -> ProviderSettings:
        """Return the already validated default provider configuration."""

        return self.providers[self.default]


class VisionSettings(ProviderGroupSettings):
    """Add the feature switch controlling Vision LLM requirements."""

    enabled: bool = True


class EmbeddingSettings(ProviderGroupSettings):
    """Describe dense embedding selection and batch/cache behavior."""

    batch_size: int = Field(gt=0)
    cache_enabled: bool = True

    @model_validator(mode="after")
    def validate_selected_embedding_dimensions(self) -> Self:
        """Require a declared vector size for the selected embedding model.

        Returns:
            The validated embedding settings.

        Raises:
            ValueError: If the selected embedding provider omits ``dimensions``.
        """

        if self.selected_provider.dimensions is None:
            raise ValueError(f"Selected embedding provider '{self.default}' must define dimensions")
        return self


class VectorStoreSettings(ConfigSection):
    """Describe the first-release vector-store schema and distance contract."""

    provider: str = Field(min_length=1)
    collection_table: str = Field(min_length=1)
    document_table: str = Field(min_length=1)
    chunk_table: str = Field(min_length=1)
    distance: str = Field(min_length=1)
    embedding_dimensions: int = Field(gt=0)


class SplitterSettings(ConfigSection):
    """Select a text splitter while preserving provider-specific parameters."""

    default: str = Field(min_length=1)
    providers: dict[str, dict[str, Any]] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_default_splitter(self) -> Self:
        """Ensure the selected splitter has a corresponding configuration.

        Returns:
            The validated splitter settings.

        Raises:
            ValueError: If ``default`` is absent from ``providers``.
        """

        if self.default not in self.providers:
            raise ValueError(
                f"Selected splitter '{self.default}' is not defined; "
                f"available splitters: {sorted(self.providers)}"
            )
        return self


class TransformStepSettings(ConfigSection):
    """Describe one ordered transform step in the ingestion pipeline.

    Transform stages are not provider-selected factories. The ingestion layer
    owns the concrete step registry and executes every enabled step in this
    order so semantic rewrite, merge, denoise, and later image captioning remain
    a pipeline concern instead of a generic pluggable provider boundary.
    """

    name: str = Field(min_length=1)
    enabled: bool = True
    prompt_path: str | None = None


class TransformPipelineSettings(ConfigSection):
    """Describe the ordered transform chain applied during ingestion."""

    steps: list[TransformStepSettings] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_step_names(self) -> Self:
        """Ensure transform orchestration cannot execute ambiguous steps.

        Returns:
            The validated transform pipeline settings.

        Raises:
            ValueError: If the same step name appears more than once.
        """

        names = [step.name for step in self.steps]
        duplicate_names = sorted({name for name in names if names.count(name) > 1})
        if duplicate_names:
            raise ValueError(f"Duplicate transform steps are not allowed: {duplicate_names}")
        return self


class RetrievalFiltersSettings(ConfigSection):
    """Define default metadata filtering behavior for retrieval."""

    include_deleted: bool = False
    default_collection: str = Field(min_length=1)


class RetrievalSettings(ConfigSection):
    """Define candidate limits, RRF parameters, and default filters."""

    query_rewrite_enabled: bool = True
    dense_top_k: int = Field(gt=0)
    sparse_top_k: int = Field(gt=0)
    fusion_top_k: int = Field(gt=0)
    final_top_k: int = Field(gt=0)
    rrf_k: int = Field(gt=0)
    filters: RetrievalFiltersSettings

    @model_validator(mode="after")
    def validate_candidate_limits(self) -> Self:
        """Ensure intermediate candidate pools can supply final results.

        Returns:
            The validated retrieval settings.

        Raises:
            ValueError: If any route or fusion limit is smaller than the final
                requested result count.
        """

        route_limits = {
            "dense_top_k": self.dense_top_k,
            "sparse_top_k": self.sparse_top_k,
            "fusion_top_k": self.fusion_top_k,
        }
        undersized = [name for name, value in route_limits.items() if value < self.final_top_k]
        if undersized:
            raise ValueError(
                "Retrieval candidate limits must be greater than or equal to "
                f"final_top_k; invalid fields: {undersized}"
            )
        return self


class RerankSettings(ConfigSection):
    """Describe reranker selection, fallback order, and Prompt location."""

    enabled: bool = True
    default: str = Field(min_length=1)
    fallback: str = Field(min_length=1)
    prompt_path: str
    top_k: int = Field(gt=0)
    providers: dict[str, ProviderSettings] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_default_reranker(self) -> Self:
        """Ensure an enabled reranker selector references a configured backend.

        Returns:
            The validated rerank settings.

        Raises:
            ValueError: If reranking is enabled and ``default`` is undefined.
        """

        if self.enabled and self.default not in self.providers:
            raise ValueError(
                f"Selected reranker '{self.default}' is not defined; "
                f"available rerankers: {sorted(self.providers)}"
            )
        return self


class EvidenceContextOptimizerSettings(ConfigSection):
    """Configure final-context optimization for public RAG responses.

    The response layer may call an LLM to turn ranked evidence blocks into an
    Agent-ready context. The switch and fallback flag live in settings so local
    evaluation can compare raw evidence against optimized context without code
    changes.
    """

    enabled: bool = True
    llm_provider: str = Field(min_length=1)
    prompt_path: str
    fallback_to_raw: bool = True


class ResponseSettings(ConfigSection):
    """Describe public response shaping after retrieval and reranking."""

    evidence_context_optimizer: EvidenceContextOptimizerSettings


class IngestionSettings(ConfigSection):
    """Describe source, Markdown, image, deduplication, and lifecycle settings."""

    raw_data_dir: str
    markdown_dir: str
    image_dir: str
    dedup: dict[str, Any]
    lifecycle: dict[str, Any]


class StorageSettings(ConfigSection):
    """Describe local index, PostgreSQL helper, and cache directories."""

    bm25_index_dir: str
    postgres_data_dir: str
    embedding_cache_dir: str
    caption_cache_dir: str
    processing_cache_dir: str


class TransformSnapshotSettings(ConfigSection):
    """Control lightweight before/after Transform evidence stored in traces.

    The ingestion Dashboard uses these settings to compare raw chunks with the
    output of each concrete Transform implementation. The trace stores bounded
    text previews instead of full chunk content so observability remains useful
    without turning trace logs into another document store.
    """

    enabled: bool = True
    max_chunks_per_step: int = Field(default=20, gt=0)
    max_chars_per_chunk: int = Field(default=800, gt=0)
    include_unchanged_chunks: bool = False


class ObservabilitySettings(ConfigSection):
    """Describe structured application and trace persistence behavior."""

    app_log_path: str
    trace_jsonl_path: str
    persist_to_postgresql: bool = True
    json_formatter: bool = True
    transform_snapshots: TransformSnapshotSettings = Field(
        default_factory=TransformSnapshotSettings
    )


class DashboardSettings(ConfigSection):
    """Describe local Streamlit availability, port, and ordered navigation."""

    enabled: bool = True
    port: int = Field(gt=0, le=65535)
    pages: list[str] = Field(min_length=1)


class EvaluationRetrievalMetricsSettings(ConfigSection):
    """Describe retrieval metric switches used by quality evaluation."""

    hit_rate_at_k: bool = True
    mrr: bool = True
    ndcg: bool = True


class EvaluationGenerationMetricsSettings(ConfigSection):
    """Describe Ragas generation metric switches used by quality evaluation."""

    faithfulness: bool = True
    answer_relevancy: bool = True
    context_precision: bool = True
    context_recall: bool = True
    answer_correctness: bool = False


class EvaluationMetricsSettings(ConfigSection):
    """Group retrieval and generation metric switches under one section."""

    retrieval: EvaluationRetrievalMetricsSettings = Field(
        default_factory=EvaluationRetrievalMetricsSettings
    )
    generation: EvaluationGenerationMetricsSettings = Field(
        default_factory=EvaluationGenerationMetricsSettings
    )


class EvaluationSettings(ConfigSection):
    """Describe the golden dataset and configured quality metrics."""

    golden_set_path: str
    llm_provider: str = Field(min_length=1)
    embedding_provider: str = Field(min_length=1)
    metrics: EvaluationMetricsSettings = Field(default_factory=EvaluationMetricsSettings)


class McpSettings(ConfigSection):
    """Describe MCP availability and the tools exposed by the server."""

    enabled: bool = True
    tools: list[str] = Field(min_length=1)


class RagSettings(BaseModel):
    """Represent the complete validated runtime configuration.

    The model deliberately mirrors every top-level section in
    ``config/settings.yaml``. Downstream code receives typed sections and never
    needs to parse YAML directly. Environment values remain external and are
    validated separately so tests and offline tooling can inspect static
    configuration without possessing production credentials.
    """

    model_config = ConfigDict(extra="forbid")

    project: ProjectSettings
    database: DatabaseSettings
    llm: ProviderGroupSettings
    vision_llm: VisionSettings
    embedding: EmbeddingSettings
    vector_store: VectorStoreSettings
    splitter: SplitterSettings
    transform: TransformPipelineSettings
    retrieval: RetrievalSettings
    rerank: RerankSettings
    response: ResponseSettings
    ingestion: IngestionSettings
    storage: StorageSettings
    observability: ObservabilitySettings
    dashboard: DashboardSettings
    evaluation: EvaluationSettings
    mcp: McpSettings

    @model_validator(mode="after")
    def validate_embedding_storage_contract(self) -> Self:
        """Ensure generated vectors fit the configured pgvector schema.

        Returns:
            The fully validated RAG settings.

        Raises:
            ValueError: If the selected embedding model's dimensions differ from
                the vector-store column dimensions.
        """

        embedding_dimensions = self.embedding.selected_provider.dimensions
        if embedding_dimensions != self.vector_store.embedding_dimensions:
            raise ValueError(
                "Embedding dimensions must match vector_store.embedding_dimensions; "
                f"embedding={embedding_dimensions}, "
                f"vector_store={self.vector_store.embedding_dimensions}"
            )
        optimizer = self.response.evidence_context_optimizer
        if optimizer.enabled and optimizer.llm_provider not in self.llm.providers:
            raise ValueError(
                "response.evidence_context_optimizer.llm_provider must reference "
                f"a configured llm provider; provider={optimizer.llm_provider}, "
                f"available={sorted(self.llm.providers)}"
            )
        if self.evaluation.llm_provider not in self.llm.providers:
            raise ValueError(
                "evaluation.llm_provider must reference a configured llm provider; "
                f"provider={self.evaluation.llm_provider}, "
                f"available={sorted(self.llm.providers)}"
            )
        if self.evaluation.embedding_provider not in self.embedding.providers:
            raise ValueError(
                "evaluation.embedding_provider must reference a configured "
                "embedding provider; "
                f"provider={self.evaluation.embedding_provider}, "
                f"available={sorted(self.embedding.providers)}"
            )
        return self

    def required_environment_variables(self) -> set[str]:
        """Collect environment references used by active runtime components.

        The database, selected chat provider, selected embedding provider, and
        enabled selected vision provider participate in startup. Inactive and
        fallback providers are intentionally excluded until they are selected,
        preventing unused integrations from blocking local operation.

        Returns:
            A deduplicated set of required environment-variable names.
        """

        required = {self.database.url_env}
        required.update(self.llm.selected_provider.environment_references())
        required.update(self.embedding.selected_provider.environment_references())
        optimizer = self.response.evidence_context_optimizer
        if optimizer.enabled:
            required.update(self.llm.providers[optimizer.llm_provider].environment_references())
        required.update(
            self.llm.providers[self.evaluation.llm_provider].environment_references()
        )
        required.update(
            self.embedding.providers[
                self.evaluation.embedding_provider
            ].environment_references()
        )
        if self.vision_llm.enabled:
            required.update(self.vision_llm.selected_provider.environment_references())
        return required

    def validate_environment(self, environ: Mapping[str, str] | None = None) -> None:
        """Verify that every active environment reference has a non-empty value.

        Args:
            environ: Environment mapping to inspect. ``None`` uses
                ``os.environ``; tests may pass an isolated mapping.

        Raises:
            ValueError: If one or more required variables are absent or contain
                only whitespace. The message lists every missing name together
                so operators can fix configuration in one pass.
        """

        source = os.environ if environ is None else environ
        missing = sorted(
            variable
            for variable in self.required_environment_variables()
            if not source.get(variable, "").strip()
        )
        if missing:
            raise ValueError(
                "Missing required environment variables for active RAG "
                f"components: {', '.join(missing)}"
            )


class PromptTemplate(ConfigSection):
    """Represent a versioned Prompt and its rendering/output contracts."""

    name: str = Field(min_length=1)
    version: int = Field(gt=0)
    description: str = Field(min_length=1)
    input_variables: list[str] = Field(min_length=1)
    system_prompt: str = Field(min_length=1)
    user_prompt: str = Field(min_length=1)
    output_schema: dict[str, Any] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_variable_contract(self) -> Self:
        """Ensure declared inputs exactly match placeholders used by the Prompt.

        Returns:
            The validated Prompt template.

        Raises:
            ValueError: If variables are duplicated, unused, or referenced by a
                template without being declared.
        """

        declared = self.input_variables
        if len(declared) != len(set(declared)):
            raise ValueError("Prompt variable contract contains duplicate names")

        formatter = Formatter()
        referenced = {
            field_name
            for template in (self.system_prompt, self.user_prompt)
            for _, field_name, _, _ in formatter.parse(template)
            if field_name
        }
        if set(declared) != referenced:
            missing_from_template = sorted(set(declared) - referenced)
            undeclared = sorted(referenced - set(declared))
            raise ValueError(
                "Prompt variable contract does not match template placeholders; "
                f"unused declarations: {missing_from_template}; "
                f"undeclared placeholders: {undeclared}"
            )
        return self


def _resolve_config_path(path: str | Path, *, repository_relative_root: Path = RAG_ROOT) -> Path:
    """Resolve absolute, current-working-directory, or RAG-relative paths.

    Args:
        path: Configured path supplied by a caller or environment variable.
        repository_relative_root: Fallback base for paths such as
            ``config/prompts/rerank_prompt.yaml``.

    Returns:
        A normalized absolute path. Existence is checked by the public loader so
        it can produce a resource-specific error message.
    """

    candidate = Path(path).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()

    working_directory_candidate = (Path.cwd() / candidate).resolve()
    if working_directory_candidate.exists():
        return working_directory_candidate
    return (repository_relative_root / candidate).resolve()


def _read_yaml_mapping(path: Path, *, resource_name: str) -> dict[str, Any]:
    """Read one YAML resource and require a mapping at the document root.

    Args:
        path: Absolute YAML file path.
        resource_name: Human-readable resource label used in error messages.

    Returns:
        Parsed top-level mapping.

    Raises:
        ValueError: If the file is missing, unreadable, invalid YAML, empty, or
            has a non-mapping document root.
    """

    if not path.is_file():
        raise ValueError(f"{resource_name} file does not exist: {path}")

    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise ValueError(f"Unable to read {resource_name} file '{path}': {error}") from error

    if not isinstance(document, dict):
        raise ValueError(f"{resource_name} file '{path}' must contain a YAML mapping at its root")
    return document


def load_settings(
    path: str | Path | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    validate_environment: bool = True,
) -> RagSettings:
    """Load, type-check, and optionally validate the RAG runtime configuration.

    Args:
        path: Explicit settings file. When omitted, ``RAG_SETTINGS_PATH`` from
            ``environ`` or ``os.environ`` is used, followed by the checked-in
            default settings path.
        environ: Environment mapping used for path selection and active secret
            validation. ``None`` uses the current process environment.
        validate_environment: Whether to require values for all environment
            references used by active components.

    Returns:
        A fully validated ``RagSettings`` instance.

    Raises:
        ValueError: If the file cannot be read, YAML structure is invalid,
            provider selectors are inconsistent, or required environment values
            are missing.
    """

    environment = os.environ if environ is None else environ
    configured_path = path or environment.get("RAG_SETTINGS_PATH") or DEFAULT_SETTINGS_PATH
    settings_path = _resolve_config_path(configured_path)
    document = _read_yaml_mapping(settings_path, resource_name="Settings")

    try:
        settings = RagSettings.model_validate(document)
    except ValidationError as error:
        raise ValueError(f"Settings validation failed for '{settings_path}': {error}") from error

    if validate_environment:
        settings.validate_environment(environment)
    return settings



def enabled_generation_metrics(settings: RagSettings) -> list[str]:
    """Return enabled Ragas generation metrics in stable evaluation order.

    Args:
        settings: Fully validated runtime settings containing
            ``evaluation.metrics.generation`` switches.

    Returns:
        Ordered Ragas metric names enabled for the next evaluation run.

    Raises:
        ValueError: If every generation metric is disabled, because an
            evaluation run without metrics cannot produce useful persisted
            results.
    """

    generation = settings.evaluation.metrics.generation
    configured_metrics = [
        ("faithfulness", generation.faithfulness),
        ("answer_relevancy", generation.answer_relevancy),
        ("context_precision", generation.context_precision),
        ("context_recall", generation.context_recall),
        ("answer_correctness", generation.answer_correctness),
    ]
    enabled = [metric_name for metric_name, is_enabled in configured_metrics if is_enabled]
    if not enabled:
        raise ValueError("At least one Ragas generation metrics switch must be enabled")
    return enabled

def load_prompt(path: str | Path) -> PromptTemplate:
    """Load and validate a versioned Prompt definition without rendering it.

    Args:
        path: Absolute, working-directory-relative, or RAG-root-relative Prompt
            YAML path.

    Returns:
        A typed ``PromptTemplate`` with a verified variable contract.

    Raises:
        ValueError: If the Prompt cannot be read, fails schema validation, or
            declares variables inconsistent with its placeholders.
    """

    prompt_path = _resolve_config_path(path)
    document = _read_yaml_mapping(prompt_path, resource_name="Prompt")

    try:
        return PromptTemplate.model_validate(document)
    except ValidationError as error:
        raise ValueError(f"Prompt validation failed for '{prompt_path}': {error}") from error
