"""Compose and execute ingestion transform steps in settings order.

``TransformPipeline`` is intentionally not a generic factory. It is the
ingestion-layer adapter that maps configured step names to project-owned
transform implementations, injects prompts and LLM clients, and applies enabled
steps serially to ordered ``Chunk`` objects.
"""

from __future__ import annotations

from collections.abc import Callable
from re import sub
from time import perf_counter
from typing import Any

from src.core.config import RagSettings, TransformStepSettings, load_prompt
from src.core.errors import ConfigurationError
from src.core.types import Chunk
from src.ingestion.transform.chunk_rewriter import ChunkRewriter
from src.ingestion.transform.denoise_transform import DenoiseTransform
from src.ingestion.transform.image_captioner import ImageCaptioner
from src.ingestion.transform.image_to_text_transform import ImageToTextTransform
from src.ingestion.transform.metadata_enricher import MetadataEnricher
from src.ingestion.transform.semantic_merge_transform import SemanticMergeTransform
from src.libs.llm import BaseLLM, LLMFactory
from src.libs.transform.base_transform import BaseTransform


class TransformPipeline:
    """Run enabled transform implementations sequentially over chunk lists."""

    _STEP_BUILDERS: dict[
        str,
        Callable[[TransformStepSettings, BaseLLM | None, BaseLLM | None], BaseTransform],
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
        vision_llm: BaseLLM | None = None,
    ) -> TransformPipeline:
        """Build the transform chain from ``settings.transform.steps``.

        Args:
            settings: Validated runtime settings loaded from
                ``config/settings.yaml`` or the versioned example.
            llm: Optional injected chat client for unit tests and offline
                execution. When omitted, model-backed steps use ``LLMFactory``.
            vision_llm: Optional injected Vision LLM used by the image-to-text
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
    ) -> list[Chunk]:
        """Apply every configured transform in order.

        Args:
            chunks: Ordered chunks produced by ``DocumentChunker``.
            context: Trace-safe runtime metadata passed unchanged to every
                transform implementation.
            step_observer: Optional best-effort callback receiving one
                trace-safe execution record after each implementation succeeds
                or fails.

        Returns:
            The final ordered chunk list after all enabled transforms complete.
        """

        output = [chunk.model_copy(deep=True) for chunk in chunks]
        for step_name, transform in zip(
            self._step_names,
            self.transforms,
            strict=True,
        ):
            input_count = len(output)
            started_at = perf_counter()
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
                        "method": "transform",
                        "provider": type(transform).__name__,
                        "error": {
                            "error_type": type(error).__name__,
                            "message": str(error),
                        },
                    },
                )
                raise
            output = next_output
            _notify_step_observer(
                step_observer,
                {
                    "name": step_name,
                    "duration_ms": (perf_counter() - started_at) * 1000,
                    "status": "success",
                    "input_count": input_count,
                    "output_count": len(output),
                    "method": "transform",
                    "provider": type(transform).__name__,
                    "error": None,
                },
            )
        return output

    @staticmethod
    def _step_builders() -> dict[
        str,
        Callable[[TransformStepSettings, BaseLLM | None, BaseLLM | None], BaseTransform],
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
            "image_to_text": _build_image_captioner,
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
        "ImageCaptioner": "image_to_text",
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


def _build_metadata_enricher(
    step: TransformStepSettings,
    llm: BaseLLM | None,
    vision_llm: BaseLLM | None,
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
    vision_llm: BaseLLM | None,
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
    vision_llm: BaseLLM | None,
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
    vision_llm: BaseLLM | None,
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
    vision_llm: BaseLLM | None,
) -> BaseTransform:
    """Create the optional image captioning step.

    Args:
        step: Settings containing the required image-to-text Prompt path.
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
    image_transform = (
        ImageToTextTransform(
            vision_llm=vision_llm,
            prompt=load_prompt(step.prompt_path),
        )
        if vision_llm is not None
        else None
    )
    return ImageCaptioner(
        image_transform=image_transform,
        enabled=vision_llm is not None,
    )
