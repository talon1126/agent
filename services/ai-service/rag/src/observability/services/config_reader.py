"""Read validated settings into Dashboard-friendly component summaries.

``ConfigReaderService`` is a thin projection layer over ``RagSettings``. It
does not instantiate providers, read secrets, or validate environment values.
Dashboard pages receive immutable DTOs that expose the currently selected
providers and key runtime paths without depending on Pydantic model internals.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from src.core.config import RagSettings, load_settings

SettingsLoader = Callable[[], RagSettings]


@dataclass(frozen=True, slots=True)
class ComponentConfig:
    """Describe one configured pluggable or pipeline component.

    Attributes:
        component: Stable component family name shown by Dashboard.
        provider: Selected implementation or pipeline name.
        model: Optional model identifier for model-backed components.
        enabled: Whether the component participates in the current runtime.
        details: Additional JSON-safe settings that are useful for operators.
    """

    component: str
    provider: str
    model: str | None = None
    enabled: bool = True
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ConfigOverview:
    """Represent the Dashboard system-overview configuration payload."""

    project_name: str
    default_collection: str
    environment: str
    components: tuple[ComponentConfig, ...]
    dashboard_pages: tuple[str, ...]
    paths: Mapping[str, str]


class ConfigReaderService:
    """Project validated settings into immutable Dashboard summaries."""

    def __init__(
        self,
        *,
        settings_loader: SettingsLoader = load_settings,
    ) -> None:
        """Store the settings loader without reading configuration eagerly.

        Args:
            settings_loader: Callable returning validated runtime settings.
                Tests may inject a preloaded settings object; production uses
                the default ``load_settings`` function.
        """

        self._settings_loader = settings_loader

    def read_overview(self) -> ConfigOverview:
        """Load settings and return current component selections.

        Returns:
            Immutable overview containing project identity, selected
            components, Dashboard page names, and relevant local paths.
        """

        settings = self._settings_loader()
        return ConfigOverview(
            project_name=settings.project.name,
            default_collection=settings.project.default_collection,
            environment=settings.project.environment,
            components=(
                self._provider_group_component("llm", settings.llm),
                self._provider_group_component(
                    "vision_llm",
                    settings.vision_llm,
                    enabled=settings.vision_llm.enabled,
                ),
                self._provider_group_component("embedding", settings.embedding),
                ComponentConfig(
                    component="splitter",
                    provider=settings.splitter.default,
                    details=dict(settings.splitter.providers[settings.splitter.default]),
                ),
                ComponentConfig(
                    component="vector_store",
                    provider=settings.vector_store.provider,
                    details={
                        "distance": settings.vector_store.distance,
                        "embedding_dimensions": settings.vector_store.embedding_dimensions,
                    },
                ),
                ComponentConfig(
                    component="reranker",
                    provider=settings.rerank.default,
                    model=_reranker_model(settings),
                    enabled=settings.rerank.enabled,
                    details={
                        "fallback": settings.rerank.fallback,
                        "top_k": settings.rerank.top_k,
                        **_reranker_details(settings),
                    },
                ),
                ComponentConfig(
                    component="transform",
                    provider="serial_pipeline",
                    details={
                        "steps": _transform_step_details(settings)
                    },
                ),
            ),
            dashboard_pages=tuple(settings.dashboard.pages),
            paths={
                "raw_data_dir": settings.ingestion.raw_data_dir,
                "markdown_dir": settings.ingestion.markdown_dir,
                "image_dir": settings.ingestion.image_dir,
                "trace_jsonl_path": settings.observability.trace_jsonl_path,
                "app_log_path": settings.observability.app_log_path,
            },
        )

    @staticmethod
    def _provider_group_component(
        component: str,
        group: Any,
        *,
        enabled: bool = True,
    ) -> ComponentConfig:
        """Create a component summary from a provider-group settings object."""

        selected = group.selected_provider
        return ComponentConfig(
            component=component,
            provider=group.default,
            model=selected.model,
            enabled=enabled,
            details={
                "fallback": getattr(group, "fallback", None),
                "timeout_seconds": selected.timeout_seconds,
                "dimensions": selected.dimensions,
            },
        )


def _provider_model(provider: Any | None) -> str | None:
    """Return a provider model identifier without assuming a concrete type."""

    return getattr(provider, "model", None) if provider is not None else None


def _reranker_model(settings: RagSettings) -> str | None:
    """Resolve the concrete model used by the selected reranker.

    Args:
        settings: Validated runtime settings containing reranker and LLM
            provider groups.

    Returns:
        The direct reranker model when configured, or the model of the LLM
        provider referenced by an LLM-backed reranker.
    """

    provider = settings.rerank.providers.get(settings.rerank.default)
    direct_model = _provider_model(provider)
    if direct_model:
        return direct_model
    llm_provider = _extra_value(provider, "llm_provider")
    if isinstance(llm_provider, str):
        return _provider_model(settings.llm.providers.get(llm_provider))
    return None


def _reranker_details(settings: RagSettings) -> dict[str, Any]:
    """Return reranker details that clarify indirect model selection.

    Args:
        settings: Validated runtime settings.

    Returns:
        JSON-safe detail fields for Dashboard display.
    """

    provider = settings.rerank.providers.get(settings.rerank.default)
    llm_provider = _extra_value(provider, "llm_provider")
    if isinstance(llm_provider, str):
        return {
            "llm_provider": llm_provider,
            "model_source": f"llm.providers.{llm_provider}",
        }
    return {}


def _transform_step_details(settings: RagSettings) -> list[dict[str, Any]]:
    """Build Dashboard rows for configured sub-transform model usage.

    Args:
        settings: Validated runtime settings.

    Returns:
        Ordered step details preserving settings order. Model-backed steps show
        their resolved provider/model, while deterministic steps explicitly
        report ``n/a`` so operators do not mistake blank cells for missing
        configuration.
    """

    rows: list[dict[str, Any]] = []
    for step in settings.transform.steps:
        provider, model, model_source = _transform_model_contract(settings, step.name)
        rows.append(
            {
                "name": step.name,
                "enabled": step.enabled,
                "provider": provider,
                "model": model,
                "model_source": model_source,
                "prompt_path": step.prompt_path,
            }
        )
    return rows


def _transform_model_contract(
    settings: RagSettings,
    step_name: str,
) -> tuple[str, str, str]:
    """Resolve provider/model labels for one transform step.

    Args:
        settings: Validated runtime settings.
        step_name: Transform step name from ``settings.transform.steps``.

    Returns:
        ``(provider, model, model_source)`` suitable for the Overview expander.
    """

    if step_name in {"rewrite_chunk", "semantic_merge"}:
        provider = settings.llm.default
        return (
            provider,
            settings.llm.selected_provider.model or "n/a",
            "llm.default",
        )
    if step_name == "image_to_text":
        provider = settings.vision_llm.default
        return (
            provider,
            settings.vision_llm.selected_provider.model or "n/a",
            "vision_llm.default",
        )
    return ("deterministic", "n/a", "deterministic")


def _extra_value(provider: Any | None, key: str) -> Any:
    """Read one provider-specific extra field from a Pydantic settings object.

    Args:
        provider: Provider settings object, or ``None``.
        key: Extra field name such as ``llm_provider``.

    Returns:
        The configured value when present; otherwise ``None``.
    """

    if provider is None:
        return None
    extra = getattr(provider, "__pydantic_extra__", None)
    if isinstance(extra, Mapping) and key in extra:
        return extra[key]
    return getattr(provider, key, None)
