"""Compose and execute ingestion transform steps in settings order.

``TransformPipeline`` is intentionally not a generic factory. It is the
ingestion-layer adapter that maps configured step names to project-owned
transform implementations, injects prompts and LLM clients, and applies enabled
steps serially to ordered ``Chunk`` objects.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from re import sub
from time import perf_counter
from typing import Any

from src.core.config import RagSettings, TransformStepSettings, load_prompt
from src.core.errors import ConfigurationError
from src.core.types import Chunk
from src.ingestion.transform.chunk_rewriter import ChunkRewriter
from src.ingestion.transform.denoise_transform import DenoiseTransform
from src.ingestion.transform.image_captioner import ImageCaptioner
from src.ingestion.transform.metadata_enricher import MetadataEnricher
from src.ingestion.transform.semantic_merge_transform import SemanticMergeTransform
from src.libs.llm import BaseLLM, BaseVisionLLM, LLMFactory
from src.libs.transform.base_transform import BaseTransform


class TransformPipeline:
    """Run enabled transform implementations sequentially over chunk lists."""

    _STEP_BUILDERS: dict[
        str,
        Callable[[TransformStepSettings, BaseLLM | None, BaseVisionLLM | None], BaseTransform],
    ]

    def __init__(
        self,
        transforms: list[BaseTransform],
        *,
        step_names: list[str] | None = None,
    ) -> None:
        """Store the concrete transform chain in execution order.

        Args:
            transforms: Already constructed transform implementations. The
                order of this list is the order used during ingestion.
            step_names: Optional settings names aligned with ``transforms``.
                Directly constructed pipelines derive snake-case names from
                implementation classes.

        Raises:
            ValueError: If explicit names do not align with the transform list.
        """

        self._transforms = list(transforms)
        if step_names is not None and len(step_names) != len(transforms):
            raise ValueError("step_names must align with transforms")
        self._step_names = (
            list(step_names)
            if step_names is not None
            else [_default_step_name(transform) for transform in transforms]
        )

    @property
    def transforms(self) -> tuple[BaseTransform, ...]:
        """Return the configured transform chain without exposing mutation.

        Returns:
            A tuple containing concrete transform objects in execution order.
            The tuple wrapper lets tests, diagnostics, and future Dashboard
            pages inspect the pipeline without mutating ingestion state.
        """

        return tuple(self._transforms)

    @classmethod
    def from_settings(
        cls,
        settings: RagSettings,
        *,
        llm: BaseLLM | None = None,
        vision_llm: BaseVisionLLM | None = None,
    ) -> TransformPipeline:
        """Build the transform chain from ``settings.transform.steps``.

        Args:
            settings: Validated runtime settings loaded from
                ``config/settings.yaml`` or the versioned example.
            llm: Optional injected chat client for unit tests and offline
                execution. When omitted, model-backed steps use ``LLMFactory``.
            vision_llm: Optional injected Vision LLM used by the image caption
                step. It is separate from the text LLM because image captioning
                is optional and may later use a provider with a multimodal SDK.

        Returns:
            A pipeline containing all enabled steps in configuration order.

        Raises:
            ConfigurationError: If an enabled step name is unknown, if a model
                step omits its prompt path, or if LLM construction fails.
        """

        shared_llm = llm
        transforms: list[BaseTransform] = []
        step_names: list[str] = []
        for step in settings.transform.steps:
            if not step.enabled:
                continue
            builder = cls._step_builders().get(step.name)
            if builder is None:
                raise ConfigurationError(
                    "Unknown transform step",
                    context={
                        "step_name": step.name,
                        "available": sorted(cls._step_builders()),
                    },
                )
            if step.name in {"rewrite_chunk", "semantic_merge"} and shared_llm is None:
                shared_llm = LLMFactory.create(settings=settings)
            active_vision_llm = vision_llm if settings.vision_llm.enabled else None
            transforms.append(builder(step, shared_llm, active_vision_llm))
            step_names.append(step.name)
        return cls(transforms, step_names=step_names)

    def run(
        self,
        chunks: list[Chunk],
        *,
        context: dict[str, Any] | None = None,
        step_observer: Callable[[dict[str, Any]], None] | None = None,
        snapshot_options: Mapping[str, Any] | None = None,
    ) -> list[Chunk]:
        """Apply every configured transform in order.

        Args:
            chunks: Ordered chunks produced by ``DocumentChunker``.
            context: Trace-safe runtime metadata passed unchanged to every
                transform implementation.
            step_observer: Optional best-effort callback receiving one
                trace-safe execution record after each implementation succeeds
                or fails.
            snapshot_options: Optional bounded diff policy. When enabled, the
                observer record receives compact before/after previews showing
                what this concrete Transform changed.

        Returns:
            The final ordered chunk list after all enabled transforms complete.
        """

        output = [chunk.model_copy(deep=True) for chunk in chunks]
        tracking_enabled = step_observer is not None
        snapshots_enabled = (
            tracking_enabled
            and _snapshot_options_enabled(snapshot_options)
        )
        for step_name, transform in zip(
            self._step_names,
            self.transforms,
            strict=True,
        ):
            input_count = len(output)
            started_at = perf_counter()
            step_input = (
                [chunk.model_copy(deep=True) for chunk in output]
                if tracking_enabled
                else []
            )
            try:
                next_output = transform.transform(output, context=context)
            except Exception as error:
                _notify_step_observer(
                    step_observer,
                    {
                        "name": step_name,
                        "duration_ms": (perf_counter() - started_at) * 1000,
                        "status": "failed",
                        "input_count": input_count,
                        "output_count": 0,
                        "changed_count": 0,
                        "unchanged_count": 0,
                        "method": "transform",
                        "provider": type(transform).__name__,
                        "error": {
                            "error_type": type(error).__name__,
                            "message": str(error),
                        },
                        "snapshots": [],
                    },
                )
                raise
            output = next_output
            snapshots = (
                _build_transform_snapshots(
                    step_input,
                    output,
                    options=snapshot_options,
                )
                if snapshots_enabled
                else []
            )
            changed_count, unchanged_count = _transform_change_counts(
                step_input,
                output,
            )
            _notify_step_observer(
                step_observer,
                {
                    "name": step_name,
                    "duration_ms": (perf_counter() - started_at) * 1000,
                    "status": "success",
                    "input_count": input_count,
                    "output_count": len(output),
                    "changed_count": changed_count,
                    "unchanged_count": unchanged_count,
                    "method": "transform",
                    "provider": type(transform).__name__,
                    "error": None,
                    "snapshots": snapshots,
                    "details": _transform_trace_details(transform),
                },
            )
        return output

    @staticmethod
    def _step_builders() -> dict[
        str,
        Callable[[TransformStepSettings, BaseLLM | None, BaseVisionLLM | None], BaseTransform],
    ]:
        """Return the fixed ingestion transform registry.

        Returns:
            A map from settings step names to concrete builders. This registry
            is deliberately local to ingestion orchestration and is not exposed
            as a provider factory.
        """

        return {
            "metadata_enrich": _build_metadata_enricher,
            "rewrite_chunk": _build_chunk_rewriter,
            "semantic_merge": _build_semantic_merge,
            "denoise": _build_denoise_transform,
            "image_captioner": _build_image_captioner,
        }


def _default_step_name(transform: BaseTransform) -> str:
    """Derive a stable trace name for a directly constructed transform.

    Args:
        transform: Concrete transform without an explicit settings step name.

    Returns:
        The settings vocabulary for built-in implementations, or a snake-case
        class name for custom injected transforms.
    """

    class_name = type(transform).__name__
    built_in_names = {
        "MetadataEnricher": "metadata_enrich",
        "ChunkRewriter": "rewrite_chunk",
        "SemanticMergeTransform": "semantic_merge",
        "DenoiseTransform": "denoise",
        "ImageCaptioner": "image_captioner",
    }
    return built_in_names.get(
        class_name,
        sub(r"(?<!^)(?=[A-Z])", "_", class_name).lower(),
    )


def _notify_step_observer(
    observer: Callable[[dict[str, Any]], None] | None,
    record: dict[str, Any],
) -> None:
    """Publish one child execution record without changing business behavior.

    Args:
        observer: Optional callback owned by the ingestion trace adapter.
        record: Trace-safe timing, count, identity, status, and error fields.

    Notes:
        Observer errors are intentionally ignored. Observability must not turn
        a successful Transform into a failed ingestion or replace the original
        Transform exception.
    """

    if observer is None:
        return
    try:
        observer(record)
    except Exception:
        return


def _transform_trace_details(transform: BaseTransform) -> dict[str, Any]:
    """Read optional transform-specific details for trace sub-stages.

    Args:
        transform: Concrete transform implementation that just finished.

    Returns:
        A trace-safe details mapping. Transforms without ``trace_details`` or
        transforms whose detail method fails return an empty mapping so
        observability cannot replace business behavior.
    """

    trace_details = getattr(transform, "trace_details", None)
    if not callable(trace_details):
        return {}
    try:
        details = trace_details()
    except Exception:
        return {}
    return dict(details) if isinstance(details, Mapping) else {}


def _build_transform_snapshots(
    before_chunks: list[Chunk],
    after_chunks: list[Chunk],
    *,
    options: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    """Build bounded before/after previews for one Transform implementation.

    Args:
        before_chunks: Deep-copied input chunks captured before the Transform
            runs.
        after_chunks: Output chunks returned by the Transform.
        options: Dashboard trace policy with ``enabled``,
            ``max_chunks_per_step``, ``max_chars_per_chunk``, and
            ``include_unchanged_chunks`` keys.

    Returns:
        Ordered snapshot dictionaries safe to store in TraceContext. The
        function never returns full chunk text, only truncated previews.
    """

    if not options or not bool(options.get("enabled", False)):
        return []
    max_chunks = _positive_int_option(options, "max_chunks_per_step", default=20)
    max_chars = _positive_int_option(options, "max_chars_per_chunk", default=800)
    include_unchanged = bool(options.get("include_unchanged_chunks", False))

    before_by_key, after_by_key = _snapshot_lookups(before_chunks, after_chunks)
    ordered_keys = list(before_by_key)
    ordered_keys.extend(key for key in after_by_key if key not in before_by_key)

    snapshots: list[dict[str, Any]] = []
    for key in ordered_keys:
        before = before_by_key.get(key)
        after = after_by_key.get(key)
        snapshot = _snapshot_for_pair(
            before,
            after,
            max_chars=max_chars,
            include_unchanged=include_unchanged,
        )
        if snapshot is None:
            continue
        snapshots.append(snapshot)
        if len(snapshots) >= max_chunks:
            break
    return snapshots


def _transform_change_counts(
    before_chunks: list[Chunk],
    after_chunks: list[Chunk],
) -> tuple[int, int]:
    """Count changed and unchanged chunk identities for one Transform step.

    Added and removed chunks count as changed. Chunks matched through stable
    source coordinates count as unchanged only when their text is identical.
    The summary remains available even when bounded snapshot capture is off.
    """

    before_by_key, after_by_key = _snapshot_lookups(before_chunks, after_chunks)
    keys = set(before_by_key) | set(after_by_key)
    unchanged_count = sum(
        1
        for key in keys
        if key in before_by_key
        and key in after_by_key
        and before_by_key[key].text == after_by_key[key].text
    )
    return len(keys) - unchanged_count, unchanged_count


def _snapshot_options_enabled(options: Mapping[str, Any] | None) -> bool:
    """Return whether snapshot capture is explicitly enabled.

    Args:
        options: Optional snapshot policy mapping from settings or tests.

    Returns:
        ``True`` only when a policy exists and its ``enabled`` flag is truthy.
        The pipeline checks this before deep-copying chunks so disabled
        observability has near-zero transform overhead.
    """

    return bool(options and options.get("enabled", False))


def _snapshot_lookups(
    before_chunks: list[Chunk],
    after_chunks: list[Chunk],
) -> tuple[dict[tuple[Any, ...], Chunk], dict[tuple[Any, ...], Chunk]]:
    """Build collision-safe chunk lookup maps for before/after comparison.

    Args:
        before_chunks: Input chunks captured before one Transform.
        after_chunks: Output chunks emitted by that Transform.

    Returns:
        Two dictionaries keyed by source coordinates when unique. If a test or
        unusual source produces duplicate coordinates, the chunk ID is appended
        to preserve each row instead of collapsing snapshots.
    """

    before_keys = [_snapshot_key(chunk) for chunk in before_chunks]
    after_keys = [_snapshot_key(chunk) for chunk in after_chunks]
    before_counts = Counter(before_keys)
    after_counts = Counter(after_keys)
    duplicate_keys = {
        key
        for key in before_keys + after_keys
        if before_counts[key] > 1 or after_counts[key] > 1
    }
    before_lookup = {
        _snapshot_lookup_key(chunk, duplicate_keys): chunk for chunk in before_chunks
    }
    after_lookup = {
        _snapshot_lookup_key(chunk, duplicate_keys): chunk for chunk in after_chunks
    }
    return before_lookup, after_lookup


def _snapshot_for_pair(
    before: Chunk | None,
    after: Chunk | None,
    *,
    max_chars: int,
    include_unchanged: bool,
) -> dict[str, Any] | None:
    """Create one changed/added/removed/unchanged preview row.

    Args:
        before: Chunk before the Transform, or ``None`` for added chunks.
        after: Chunk after the Transform, or ``None`` for removed chunks.
        max_chars: Maximum number of preview characters per side.
        include_unchanged: Whether identical chunks should still be emitted.

    Returns:
        A trace snapshot dictionary, or ``None`` when the chunk is unchanged
        and unchanged snapshots are disabled.
    """

    if before is None and after is None:
        return None
    active = after or before
    assert active is not None
    before_text = before.text if before is not None else ""
    after_text = after.text if after is not None else ""
    if before is None:
        change_type = "added"
    elif after is None:
        change_type = "removed"
    elif before_text != after_text:
        change_type = "changed"
    else:
        change_type = "unchanged"
    if change_type == "unchanged" and not include_unchanged:
        return None

    before_preview, before_truncated = _preview_text(before_text, max_chars)
    after_preview, after_truncated = _preview_text(after_text, max_chars)
    return {
        "chunk_id": active.id,
        "chunk_index": active.chunk_index,
        "change_type": change_type,
        "before_preview": before_preview,
        "after_preview": after_preview,
        "before_truncated": before_truncated,
        "after_truncated": after_truncated,
    }


def _snapshot_lookup_key(
    chunk: Chunk,
    duplicate_source_keys: set[tuple[Any, ...]],
) -> tuple[Any, ...]:
    """Return the final lookup key for one chunk snapshot comparison.

    Args:
        chunk: Chunk to place into a before/after lookup.
        duplicate_source_keys: Source-coordinate keys that are not unique in at
            least one side of the comparison.

    Returns:
        The source key alone when it is unique, otherwise the source key plus
        the stable chunk ID to avoid overwriting a sibling chunk.
    """

    source_key = _snapshot_key(chunk)
    if source_key in duplicate_source_keys:
        return (*source_key, "chunk_id", chunk.id)
    return source_key


def _snapshot_key(chunk: Chunk) -> tuple[Any, ...]:
    """Return a stable chunk matching key across ID-changing transforms.

    Args:
        chunk: Chunk emitted by the business chunker or a Transform.

    Returns:
        A tuple based on source coordinates when available, otherwise the chunk
        ID. Source coordinates keep rewrite snapshots readable even when the
        rewrite step intentionally changes the content-derived chunk ID.
    """

    return (
        chunk.metadata.get("document_id"),
        chunk.start_offset,
        chunk.end_offset,
        chunk.chunk_index,
        _hashable_source_value(chunk.metadata.get("section_path")),
    )


def _hashable_source_value(value: Any) -> Any:
    """Normalize source metadata values so they can participate in dict keys.

    Args:
        value: Optional value copied from chunk metadata.

    Returns:
        Tuples for lists and dictionaries, or the original scalar value.
    """

    if isinstance(value, list | tuple):
        return tuple(_hashable_source_value(item) for item in value)
    if isinstance(value, dict):
        return tuple(
            (key, _hashable_source_value(item))
            for key, item in sorted(value.items())
        )
    return value


def _preview_text(text: str, max_chars: int) -> tuple[str, bool]:
    """Return a bounded text preview and whether truncation occurred.

    Args:
        text: Full chunk text from one side of a Transform comparison.
        max_chars: Maximum preview length.

    Returns:
        A ``(preview, truncated)`` tuple. Empty text remains empty and is never
        marked as truncated.
    """

    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def _positive_int_option(
    options: Mapping[str, Any],
    key: str,
    *,
    default: int,
) -> int:
    """Read a positive integer option while tolerating loose config mappings.

    Args:
        options: Snapshot policy mapping from settings or tests.
        key: Option name to normalize.
        default: Fallback when the option is missing or invalid.

    Returns:
        A positive integer suitable for bounding trace payloads.
    """

    value = options.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return default
    return value


def _build_metadata_enricher(
    step: TransformStepSettings,
    llm: BaseLLM | None,
    vision_llm: BaseVisionLLM | None,
) -> BaseTransform:
    """Create the metadata enrichment step.

    Args:
        step: Enabled transform step settings. Extra fields are ignored by this
            deterministic transform.
        llm: Unused optional shared LLM argument accepted for a uniform builder
            signature.

    Returns:
        A ``MetadataEnricher`` instance.
    """

    del step, llm, vision_llm
    return MetadataEnricher()


def _build_chunk_rewriter(
    step: TransformStepSettings,
    llm: BaseLLM | None,
    vision_llm: BaseVisionLLM | None,
) -> BaseTransform:
    """Create the LLM-backed chunk rewrite step.

    Args:
        step: Settings containing the required rewrite Prompt path.
        llm: Shared chat client injected by the pipeline.

    Returns:
        A ``ChunkRewriter`` configured with the versioned rewrite Prompt.

    Raises:
        ConfigurationError: If the step has no Prompt path or no LLM client.
    """

    del vision_llm
    if step.prompt_path is None:
        raise ConfigurationError(
            "Transform step requires prompt_path",
            context={"step_name": step.name},
        )
    if llm is None:
        raise ConfigurationError(
            "Transform step requires an LLM client",
            context={"step_name": step.name},
        )
    return ChunkRewriter(llm=llm, prompt=load_prompt(step.prompt_path))


def _build_semantic_merge(
    step: TransformStepSettings,
    llm: BaseLLM | None,
    vision_llm: BaseVisionLLM | None,
) -> BaseTransform:
    """Create the LLM-backed semantic merge step.

    Args:
        step: Settings containing the required semantic-merge Prompt path.
        llm: Shared chat client injected by the pipeline.

    Returns:
        A ``SemanticMergeTransform`` configured with the merge Prompt.

    Raises:
        ConfigurationError: If the step has no Prompt path or no LLM client.
    """

    del vision_llm
    if step.prompt_path is None:
        raise ConfigurationError(
            "Transform step requires prompt_path",
            context={"step_name": step.name},
        )
    if llm is None:
        raise ConfigurationError(
            "Transform step requires an LLM client",
            context={"step_name": step.name},
        )
    return SemanticMergeTransform(llm=llm, prompt=load_prompt(step.prompt_path))


def _build_denoise_transform(
    step: TransformStepSettings,
    llm: BaseLLM | None,
    vision_llm: BaseVisionLLM | None,
) -> BaseTransform:
    """Create the deterministic denoise step.

    Args:
        step: Enabled transform step settings. Extra fields are ignored by this
            deterministic transform.
        llm: Unused optional shared LLM argument accepted for a uniform builder
            signature.

    Returns:
        A ``DenoiseTransform`` instance.
    """

    del step, llm, vision_llm
    return DenoiseTransform()


def _build_image_captioner(
    step: TransformStepSettings,
    llm: BaseLLM | None,
    vision_llm: BaseVisionLLM | None,
) -> BaseTransform:
    """Create the optional image captioning step.

    Args:
        step: Settings containing the required image caption Prompt path.
        llm: Unused text LLM accepted for a uniform builder signature.
        vision_llm: Optional multimodal client. When absent, the captioner is
            disabled so text-only ingestion and local tests remain usable.

    Returns:
        An ``ImageCaptioner`` that either generates captions or records skipped
        status for chunks containing image references.

    Raises:
        ConfigurationError: If the step is enabled but omits its Prompt path.
    """

    del llm
    if step.prompt_path is None:
        raise ConfigurationError(
            "Transform step requires prompt_path",
            context={"step_name": step.name},
        )
    return ImageCaptioner(
        vision_llm=vision_llm,
        prompt=load_prompt(step.prompt_path),
        enabled=vision_llm is not None,
    )
