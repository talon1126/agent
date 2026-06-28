"""Adapt golden-set generation records to optional Ragas metrics.

``RagasEvaluator`` is the Phase G bridge between project-owned evaluation data
contracts and the optional ``ragas`` package. The adapter deliberately avoids a
module-level Ragas import because normal local development uses only the
``dev`` extra, while Ragas is declared under the optional ``evaluation`` extra.
This keeps ordinary unit tests fast and dependency-light, and allows real Ragas
smoke tests to stay behind the ``external`` marker.

The adapter does not run retrieval, generate answers, persist results, or
choose strategy variants. Those responsibilities belong to the future
``EvaluationRunner`` and ``EvaluationService``. This file only validates
aligned golden/prediction records, converts them into Ragas-compatible rows,
invokes a supplied or lazily imported backend, and normalizes usable numeric
metric values.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import uuid4

from src.core.errors import ProviderError
from src.libs.embedding.base_embedding import BaseEmbedding
from src.libs.llm.base_llm import BaseLLM, ChatMessage

EvaluationRecord = Mapping[str, Any]
RagasModelCallObserver = Callable[[Mapping[str, Any]], None]
DEFAULT_RAGAS_METRICS = ("faithfulness", "answer_relevancy")


@dataclass(frozen=True, slots=True)
class RagasRuntimeConfig:
    """Describe executor limits shared by fake and real Ragas backends.

    Args:
        timeout_seconds: Maximum seconds Ragas should allow one metric job to
            run before timing out.
        max_workers: Maximum number of worker jobs Ragas may run concurrently.
    """

    timeout_seconds: int = 300
    max_workers: int = 8

    def __post_init__(self) -> None:
        """Reject invalid executor limits before reaching Ragas internals."""

        if self.timeout_seconds <= 0:
            raise ValueError("Ragas timeout_seconds must be greater than zero")
        if self.max_workers <= 0:
            raise ValueError("Ragas max_workers must be greater than zero")

    def as_mapping(self) -> dict[str, int]:
        """Return a test-friendly representation of the runtime settings."""

        return {
            "timeout_seconds": self.timeout_seconds,
            "max_workers": self.max_workers,
        }


class RagasEvaluateFn(Protocol):
    """Describe the minimal callable boundary used to invoke Ragas.

    Tests inject a fake implementation of this protocol so unit coverage can
    verify adapter behavior without importing the real ``ragas`` dependency.
    The real loader returns a callable with the same shape.
    """

    def __call__(
        self,
        rows: list[dict[str, Any]],
        *,
        metrics: tuple[str, ...],
        run_config: Mapping[str, int] | None = None,
    ) -> Any:
        """Evaluate prepared Ragas rows and return a raw metric result."""


@dataclass(frozen=True, slots=True)
class RagasEvaluator:
    """Evaluate generation quality with Ragas-compatible metrics.

    Args:
        metric_names: Metric names requested from Ragas. ``None`` selects the
            first-version dashboard metrics: faithfulness and answer relevancy.
        evaluate_fn: Optional backend callable. Tests should inject a fake
            callable; production code leaves this as ``None`` so the adapter
            lazily imports ``ragas`` when evaluation actually runs.
        llm_client: Optional project LLM client wrapped and passed to Ragas so
            generation metrics do not rely on Ragas defaults.
        embedding_client: Optional project embedding client wrapped and passed
            to Ragas for metrics such as answer relevancy.
    """

    metric_names: tuple[str, ...] = DEFAULT_RAGAS_METRICS
    evaluate_fn: RagasEvaluateFn | None = field(default=None, repr=False, compare=False)
    llm_client: BaseLLM | None = field(default=None, repr=False, compare=False)
    embedding_client: BaseEmbedding | None = field(default=None, repr=False, compare=False)
    model_call_observer: RagasModelCallObserver | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    runtime_config: RagasRuntimeConfig = field(
        default_factory=RagasRuntimeConfig,
        repr=False,
        compare=False,
    )

    def __init__(
        self,
        *,
        metric_names: Sequence[str] | None = None,
        evaluate_fn: RagasEvaluateFn | None = None,
        llm_client: BaseLLM | None = None,
        embedding_client: BaseEmbedding | None = None,
        model_call_observer: RagasModelCallObserver | None = None,
        timeout_seconds: int = 300,
        max_workers: int = 8,
    ) -> None:
        """Create a Ragas adapter with deterministic metric-name validation.

        Args:
            metric_names: Optional ordered metric names. Blank names are
                rejected because downstream persistence uses names as stable
                metric keys.
            evaluate_fn: Optional callable replacing the real Ragas backend.
            llm_client: Optional project LLM client for Ragas generation
                metric prompts.
            embedding_client: Optional project embedding client for Ragas
                semantic metrics.
            model_call_observer: Optional trace-safe callback for LLM and
                embedding calls made inside Ragas metric execution.
            timeout_seconds: Maximum seconds allowed for one Ragas metric job.
            max_workers: Maximum number of concurrent Ragas worker jobs.
        """

        object.__setattr__(
            self,
            "metric_names",
            _normalize_metric_names(
                DEFAULT_RAGAS_METRICS if metric_names is None else metric_names
            ),
        )
        object.__setattr__(self, "evaluate_fn", evaluate_fn)
        object.__setattr__(self, "llm_client", llm_client)
        object.__setattr__(self, "embedding_client", embedding_client)
        object.__setattr__(self, "model_call_observer", model_call_observer)
        object.__setattr__(
            self,
            "runtime_config",
            RagasRuntimeConfig(
                timeout_seconds=timeout_seconds,
                max_workers=max_workers,
            ),
        )

    def evaluate(
        self,
        dataset: Sequence[EvaluationRecord],
        predictions: Sequence[EvaluationRecord],
    ) -> dict[str, float]:
        """Evaluate generated answers against golden references.

        Args:
            dataset: Golden records containing ``question`` and
                ``golden_answer`` fields.
            predictions: Aligned prediction records containing generated
                ``answer`` text and non-empty ``contexts``.

        Returns:
            Numeric Ragas metric scores keyed by stable metric name.

        Raises:
            ValueError: If the dataset and predictions are misaligned or a
                required field is missing.
            ProviderError: If the real or injected Ragas backend fails.
        """

        result = self.evaluate_with_samples(dataset, predictions)
        return dict(result["metrics"])

    def evaluate_with_samples(
        self,
        dataset: Sequence[EvaluationRecord],
        predictions: Sequence[EvaluationRecord],
    ) -> dict[str, Any]:
        """Evaluate aggregate scores and preserve per-sample metric rows.

        Args:
            dataset: Golden records containing ``question`` and reference text.
            predictions: Aligned prediction records containing answer text and
                retrieved contexts.

        Returns:
            A mapping with ``metrics`` for run-level scores and
            ``sample_metrics`` aligned to the input dataset when the backend
            exposes per-row values.

        Raises:
            ValueError: If the dataset and predictions are misaligned or a
                required field is missing.
            ProviderError: If the real or injected Ragas backend fails.
        """

        rows = _build_ragas_rows(dataset, predictions)
        backend = self.evaluate_fn or _load_ragas_backend(
            llm_client=self.llm_client,
            embedding_client=self.embedding_client,
            runtime_config=self.runtime_config,
            observer=self.model_call_observer,
        )
        try:
            raw_result = backend(
                rows,
                metrics=self.metric_names,
                run_config=self.runtime_config.as_mapping(),
            )
        except Exception as error:
            raise ProviderError(
                "Ragas evaluation failed",
                context={"metrics": list(self.metric_names), "sample_count": len(rows)},
                cause=error,
            ) from error
        return {
            "metrics": _normalize_ragas_result(raw_result, self.metric_names),
            "sample_metrics": _sample_metrics_from_ragas_result(
                raw_result,
                self.metric_names,
                expected_count=len(rows),
            ),
        }


def _build_ragas_rows(
    dataset: Sequence[EvaluationRecord],
    predictions: Sequence[EvaluationRecord],
) -> list[dict[str, Any]]:
    """Convert aligned project records into Ragas-compatible row mappings.

    Args:
        dataset: Golden records in the project fixture schema.
        predictions: Generated answer records aligned by list position.

    Returns:
        A list of dictionaries with ``question``, ``answer``, ``contexts``, and
        ``ground_truth`` keys.

    Raises:
        ValueError: If record counts differ or any row lacks required text.
    """

    if len(dataset) != len(predictions):
        raise ValueError("dataset and predictions must contain the same number of records")

    rows: list[dict[str, Any]] = []
    for golden_record, prediction_record in zip(dataset, predictions, strict=True):
        rows.append(
            {
                "question": _required_text(golden_record, "question"),
                "answer": _prediction_answer(prediction_record),
                "contexts": _prediction_contexts(prediction_record),
                "ground_truth": _golden_answer(golden_record),
            }
        )
    return rows


def _load_ragas_backend(
    *,
    llm_client: BaseLLM | None = None,
    embedding_client: BaseEmbedding | None = None,
    runtime_config: RagasRuntimeConfig,
    observer: RagasModelCallObserver | None = None,
) -> RagasEvaluateFn:
    """Load the optional Ragas package only when a real evaluation runs.

    Args:
        llm_client: Optional project LLM client wrapped as a Ragas LLM.
        embedding_client: Optional project embedding client wrapped as Ragas
            embeddings.
        runtime_config: Configured executor limits used to build Ragas
            ``RunConfig`` for the real backend.
        observer: Optional trace-safe callback receiving model-call events.

    Returns:
        A callable that accepts project-normalized rows and forwards them to
        ``ragas.evaluate``.

    Raises:
        ProviderError: If optional dependencies from the ``evaluation`` extra
            are not installed.
    """

    try:
        from datasets import Dataset
        from langchain_core.outputs import Generation, LLMResult
        from ragas import evaluate as ragas_evaluate
        from ragas import metrics as ragas_metrics
        from ragas.embeddings.base import BaseRagasEmbeddings
        from ragas.llms.base import BaseRagasLLM
        from ragas.run_config import RunConfig
    except ImportError as error:
        raise ProviderError(
            "Ragas is not installed. Install the evaluation extra before running "
            "real Ragas metrics.",
            context={"extra": "evaluation"},
            cause=error,
        ) from error

    class ProjectRagasLLM(BaseRagasLLM):
        """Adapt the project ``BaseLLM`` contract to Ragas LLM prompts."""

        def __init__(self, client: BaseLLM) -> None:
            """Store the project client and initialize Ragas retry settings."""

            super().__init__()
            self._client = client
            self.set_run_config(
                RunConfig(
                    timeout=runtime_config.timeout_seconds,
                    max_workers=runtime_config.max_workers,
                )
            )

        def generate_text(
            self,
            prompt: Any,
            n: int = 1,
            temperature: float = 0.01,
            stop: list[str] | None = None,
            callbacks: Any = None,
        ) -> Any:
            """Generate one or more Ragas completions synchronously."""

            del callbacks
            prompt_text = _prompt_to_text(prompt)
            generations: list[Generation] = []
            call_id = _model_call_id("llm")
            started_at = time.monotonic()
            _emit_model_event(
                observer,
                {
                    "event": "ragas_llm_call_started",
                    "call_id": call_id,
                    "step": "ragas_llm",
                    "status": "started",
                    "prompt_chars": len(prompt_text),
                    "n": max(n, 1),
                    "temperature": temperature,
                    "has_stop": bool(stop),
                },
            )
            try:
                provider = None
                model = None
                output_chars = 0
                for _ in range(max(n, 1)):
                    response = self._client.chat(
                        [ChatMessage(role="user", content=prompt_text)]
                    )
                    text = _apply_stop_tokens(response.content, stop)
                    provider = response.provider
                    model = response.model
                    output_chars += len(text)
                    generations.append(
                        Generation(
                            text=text,
                            generation_info={
                                "finish_reason": "stop",
                                "provider": response.provider,
                                "model": response.model,
                            },
                        )
                    )
            except Exception as error:
                _emit_model_event(
                    observer,
                    _failed_model_event(
                        call_id=call_id,
                        event="ragas_llm_call_failed",
                        step="ragas_llm",
                        started_at=started_at,
                        error=error,
                        extra={"prompt_chars": len(prompt_text)},
                    ),
                )
                raise
            _emit_model_event(
                observer,
                {
                    "event": "ragas_llm_call_done",
                    "call_id": call_id,
                    "step": "ragas_llm",
                    "status": "success",
                    "duration_ms": _elapsed_ms(started_at),
                    "provider": provider,
                    "model": model,
                    "output_chars": output_chars,
                },
            )
            return LLMResult(generations=[generations])

        async def agenerate_text(
            self,
            prompt: Any,
            n: int = 1,
            temperature: float | None = 0.01,
            stop: list[str] | None = None,
            callbacks: Any = None,
        ) -> Any:
            """Generate Ragas completions without blocking the event loop."""

            return await asyncio.to_thread(
                self.generate_text,
                prompt,
                n=n,
                temperature=0.01 if temperature is None else temperature,
                stop=stop,
                callbacks=callbacks,
            )

        def is_finished(self, response: Any) -> bool:
            """Treat project LLM calls as complete when they return text."""

            return bool(getattr(response, "generations", None))

    class ProjectRagasEmbeddings(BaseRagasEmbeddings):
        """Adapt the project ``BaseEmbedding`` contract to Ragas embeddings."""

        def __init__(self, client: BaseEmbedding) -> None:
            """Store the project embedding client and initialize Ragas retries."""

            super().__init__()
            self._client = client
            self.set_run_config(
                RunConfig(
                    timeout=runtime_config.timeout_seconds,
                    max_workers=runtime_config.max_workers,
                )
            )

        def embed_query(self, text: str) -> list[float]:
            """Embed one Ragas query string with the project embedding client."""

            call_id = _model_call_id("emb")
            started_at = time.monotonic()
            _emit_embedding_started(
                observer,
                call_id=call_id,
                method="embed_query",
                texts=[text],
            )
            try:
                vector = self._client.embed(text)
            except Exception as error:
                _emit_model_event(
                    observer,
                    _failed_model_event(
                        call_id=call_id,
                        event="ragas_embedding_call_failed",
                        step="ragas_embedding",
                        started_at=started_at,
                        error=error,
                        extra={"method": "embed_query"},
                    ),
                )
                raise
            _emit_embedding_done(
                observer,
                call_id=call_id,
                method="embed_query",
                vectors=[vector],
                started_at=started_at,
                client=self._client,
            )
            return vector

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            """Embed Ragas context strings in one provider batch."""

            documents = list(texts)
            call_id = _model_call_id("emb")
            started_at = time.monotonic()
            _emit_embedding_started(
                observer,
                call_id=call_id,
                method="embed_documents",
                texts=documents,
            )
            try:
                vectors = self._client.embed_batch(documents)
            except Exception as error:
                _emit_model_event(
                    observer,
                    _failed_model_event(
                        call_id=call_id,
                        event="ragas_embedding_call_failed",
                        step="ragas_embedding",
                        started_at=started_at,
                        error=error,
                        extra={"method": "embed_documents", "text_count": len(documents)},
                    ),
                )
                raise
            _emit_embedding_done(
                observer,
                call_id=call_id,
                method="embed_documents",
                vectors=vectors,
                started_at=started_at,
                client=self._client,
            )
            return vectors

        async def aembed_query(self, text: str) -> list[float]:
            """Embed one Ragas query string asynchronously."""

            return await asyncio.to_thread(self.embed_query, text)

        async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
            """Embed Ragas context strings asynchronously."""

            return await asyncio.to_thread(self.embed_documents, list(texts))

    ragas_llm = ProjectRagasLLM(llm_client) if llm_client is not None else None
    ragas_embeddings = (
        ProjectRagasEmbeddings(embedding_client)
        if embedding_client is not None
        else None
    )

    def _run_ragas(
        rows: list[dict[str, Any]],
        *,
        metrics: tuple[str, ...],
        run_config: Mapping[str, int] | None = None,
    ) -> Any:
        """Convert rows to a Hugging Face dataset and call ``ragas.evaluate``."""

        del run_config
        dataset = Dataset.from_list([_to_ragas_v02_row(row) for row in rows])
        metric_objects = [_metric_object(ragas_metrics, metric_name) for metric_name in metrics]
        return ragas_evaluate(
            dataset,
            metrics=metric_objects,
            llm=ragas_llm,
            embeddings=ragas_embeddings,
            show_progress=False,
            run_config=RunConfig(
                timeout=runtime_config.timeout_seconds,
                max_workers=runtime_config.max_workers,
            ),
        )

    return _run_ragas



def _load_ragas_backend_for_test(
    *,
    llm_client: BaseLLM,
    embedding_client: BaseEmbedding,
    observer: RagasModelCallObserver,
    timeout_seconds: int,
    max_workers: int,
) -> RagasEvaluateFn:
    """Create a no-dependency fake Ragas backend for observer unit tests."""

    runtime_config = RagasRuntimeConfig(
        timeout_seconds=timeout_seconds,
        max_workers=max_workers,
    )

    def _run_fake(
        rows: list[dict[str, Any]],
        *,
        metrics: tuple[str, ...],
        run_config: Mapping[str, int] | None = None,
    ) -> dict[str, float]:
        """Exercise project LLM and embedding calls without importing Ragas."""

        del run_config
        runtime_config.as_mapping()
        call_id = _model_call_id("llm")
        started_at = time.monotonic()
        prompt_text = "\n".join(row["question"] for row in rows)
        _emit_model_event(
            observer,
            {
                "event": "ragas_llm_call_started",
                "call_id": call_id,
                "step": "ragas_llm",
                "status": "started",
                "prompt_chars": len(prompt_text),
                "n": 1,
                "temperature": 0.01,
                "has_stop": False,
            },
        )
        response = llm_client.chat([ChatMessage(role="user", content=prompt_text)])
        _emit_model_event(
            observer,
            {
                "event": "ragas_llm_call_done",
                "call_id": call_id,
                "step": "ragas_llm",
                "status": "success",
                "duration_ms": _elapsed_ms(started_at),
                "provider": response.provider,
                "model": response.model,
                "output_chars": len(response.content),
            },
        )
        documents = [context for row in rows for context in row["contexts"]]
        emb_call_id = _model_call_id("emb")
        emb_started_at = time.monotonic()
        _emit_embedding_started(
            observer,
            call_id=emb_call_id,
            method="embed_documents",
            texts=documents,
        )
        vectors = embedding_client.embed_batch(documents)
        _emit_embedding_done(
            observer,
            call_id=emb_call_id,
            method="embed_documents",
            vectors=vectors,
            started_at=emb_started_at,
            client=embedding_client,
        )
        return {metric_name: 1.0 for metric_name in metrics}

    return _run_fake


def _model_call_id(prefix: str) -> str:
    """Return a short trace identifier for one Ragas model call."""

    return f"{prefix}-{uuid4().hex}"


def _elapsed_ms(started_at: float) -> float:
    """Return elapsed milliseconds for model-call observer events."""

    return max((time.monotonic() - started_at) * 1000, 0.0)


def _emit_model_event(
    observer: RagasModelCallObserver | None,
    event: Mapping[str, Any],
) -> None:
    """Send one trace-safe model-call event when an observer is configured."""

    if observer is not None:
        observer(dict(event))


def _emit_embedding_started(
    observer: RagasModelCallObserver | None,
    *,
    call_id: str,
    method: str,
    texts: Sequence[str],
) -> None:
    """Emit a safe embedding start event without storing source text."""

    _emit_model_event(
        observer,
        {
            "event": "ragas_embedding_call_started",
            "call_id": call_id,
            "step": "ragas_embedding",
            "status": "started",
            "method": method,
            "text_count": len(texts),
            "total_chars": sum(len(text) for text in texts),
        },
    )


def _emit_embedding_done(
    observer: RagasModelCallObserver | None,
    *,
    call_id: str,
    method: str,
    vectors: Sequence[Sequence[float]],
    started_at: float,
    client: BaseEmbedding,
) -> None:
    """Emit a safe embedding success event without storing vector values."""

    first_vector = vectors[0] if vectors else []
    _emit_model_event(
        observer,
        {
            "event": "ragas_embedding_call_done",
            "call_id": call_id,
            "step": "ragas_embedding",
            "status": "success",
            "method": method,
            "duration_ms": _elapsed_ms(started_at),
            "provider": getattr(client, "provider", None),
            "model": getattr(client, "model", None),
            "vector_count": len(vectors),
            "dimension": len(first_vector),
        },
    )


def _failed_model_event(
    *,
    call_id: str,
    event: str,
    step: str,
    started_at: float,
    error: Exception,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a trace-safe failure event for a Ragas model call."""

    return {
        "event": event,
        "call_id": call_id,
        "step": step,
        "status": "failed",
        "duration_ms": _elapsed_ms(started_at),
        "error_type": error.__class__.__name__,
        "error_message": str(error),
        **dict(extra or {}),
    }

def _prompt_to_text(prompt: Any) -> str:
    """Convert a Ragas/LangChain prompt value into non-empty text."""

    if hasattr(prompt, "to_string"):
        text = prompt.to_string()
    else:
        text = str(prompt)
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Ragas prompt must render to non-empty text")
    return text.strip()


def _apply_stop_tokens(text: str, stop: Sequence[str] | None) -> str:
    """Apply optional Ragas stop tokens to project LLM output."""

    output = text
    for token in stop or ():
        if token:
            output = output.split(token, 1)[0]
    return output


def _to_ragas_v02_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Convert the project row contract into Ragas 0.2 single-turn columns.

    The project keeps the older, easy-to-read row names
    ``question/answer/contexts/ground_truth`` inside tests and adapters. The
    optional real backend translates those names at the boundary because Ragas
    0.2 expects ``user_input/response/retrieved_contexts/reference``.
    """

    return {
        "user_input": row["question"],
        "response": row["answer"],
        "retrieved_contexts": row["contexts"],
        "reference": row["ground_truth"],
    }


def _metric_object(ragas_metrics: Any, metric_name: str) -> Any:
    """Resolve a configured metric name from ``ragas.metrics``.

    Args:
        ragas_metrics: Imported ``ragas.metrics`` module.
        metric_name: Stable metric key from settings or defaults.

    Returns:
        The metric object consumed by ``ragas.evaluate``.

    Raises:
        ProviderError: If the installed Ragas version does not expose the
            requested metric.
    """

    try:
        return getattr(ragas_metrics, metric_name)
    except AttributeError as error:
        raise ProviderError(
            "Ragas metric is not available in the installed package",
            context={"metric": metric_name},
            cause=error,
        ) from error


def _normalize_ragas_result(
    raw_result: Any,
    metric_names: Sequence[str],
) -> dict[str, float]:
    """Normalize common Ragas result shapes into ``metric -> float`` mapping.

    Args:
        raw_result: Value returned by the injected backend or real Ragas.
        metric_names: Metrics that must be present in the result.

    Returns:
        Finite float scores keyed by requested metric name. Metrics that Ragas
        reports as ``NaN`` or infinite are skipped because individual
        generation metrics can be unavailable for a sample set even when other
        metrics are still useful.

    Raises:
        ValueError: If a required metric is missing, non-numeric, or every
            requested metric is unavailable.
    """

    metric_values: dict[str, Any] = {}
    if isinstance(raw_result, Mapping):
        metric_values.update(raw_result)
    elif hasattr(raw_result, "scores") and isinstance(raw_result.scores, Mapping):
        metric_values.update(raw_result.scores)
    elif hasattr(raw_result, "to_pandas"):
        metric_values.update(_metrics_from_dataframe(raw_result.to_pandas(), metric_names))
    else:
        raise ValueError("Ragas result must be a mapping, expose scores, or support to_pandas()")

    normalized: dict[str, float] = {}
    unavailable_metrics: dict[str, str] = {}
    for metric_name in metric_names:
        if metric_name not in metric_values:
            raise ValueError(f"Ragas result is missing metric: {metric_name}")
        try:
            normalized[metric_name] = _finite_float(
                metric_values[metric_name],
                field_name=metric_name,
            )
        except ValueError as error:
            unavailable_metrics[metric_name] = str(error)
    if not normalized:
        joined = "; ".join(
            f"{metric_name}: {reason}"
            for metric_name, reason in unavailable_metrics.items()
        )
        raise ValueError(f"Ragas returned no finite metrics: {joined}")
    return normalized


def _metrics_from_dataframe(dataframe: Any, metric_names: Sequence[str]) -> dict[str, float]:
    """Read metric averages from a pandas-like Ragas result dataframe."""

    values: dict[str, Any] = {}
    for metric_name in metric_names:
        try:
            column = dataframe[metric_name]
        except Exception as error:
            raise ValueError(f"Ragas result is missing metric: {metric_name}") from error
        values[metric_name] = _average_numeric(column, field_name=metric_name)
    return values


def _sample_metrics_from_ragas_result(
    raw_result: Any,
    metric_names: Sequence[str],
    *,
    expected_count: int,
) -> tuple[dict[str, float], ...]:
    """Read per-sample metric values from supported Ragas result shapes.

    Args:
        raw_result: Raw value returned by the real or injected Ragas backend.
        metric_names: Metric names requested for the run.
        expected_count: Number of golden samples used to validate alignment.

    Returns:
        A tuple aligned to the input samples. Empty dictionaries are preserved
        for rows where Ragas returned only non-finite values. An empty tuple
        means the backend did not expose row-level metrics.

    Raises:
        ValueError: If an explicit sample metric payload has the wrong length
            or shape.
    """

    if isinstance(raw_result, Mapping):
        raw_sample_metrics = raw_result.get("sample_metrics")
        if raw_sample_metrics is None:
            return ()
        return _normalize_sample_metric_rows(
            raw_sample_metrics,
            metric_names,
            expected_count=expected_count,
        )
    if hasattr(raw_result, "to_pandas"):
        dataframe = raw_result.to_pandas()
        return _sample_metrics_from_dataframe(
            dataframe,
            metric_names,
            expected_count=expected_count,
        )
    return ()


def _sample_metrics_from_dataframe(
    dataframe: Any,
    metric_names: Sequence[str],
    *,
    expected_count: int,
) -> tuple[dict[str, float], ...]:
    """Read one metric mapping per dataframe row from a Ragas result."""

    if not hasattr(dataframe, "to_dict"):
        return ()
    rows = dataframe.to_dict("records")
    return _normalize_sample_metric_rows(
        rows,
        metric_names,
        expected_count=expected_count,
        skip_missing_metrics=True,
    )


def _normalize_sample_metric_rows(
    rows: Any,
    metric_names: Sequence[str],
    *,
    expected_count: int,
    skip_missing_metrics: bool = False,
) -> tuple[dict[str, float], ...]:
    """Validate and normalize row-level metric mappings."""

    if not isinstance(rows, Sequence) or isinstance(rows, str | bytes):
        raise ValueError("sample_metrics must be a sequence of metric mappings")
    if len(rows) != expected_count:
        raise ValueError("sample_metrics must match dataset sample count")
    normalized_rows: list[dict[str, float]] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("sample_metrics must contain metric mappings")
        normalized: dict[str, float] = {}
        for metric_name in metric_names:
            if metric_name not in row:
                if skip_missing_metrics:
                    continue
                raise ValueError(f"sample_metrics row is missing metric: {metric_name}")
            try:
                normalized[metric_name] = _finite_float(
                    row[metric_name],
                    field_name=metric_name,
                )
            except ValueError:
                continue
        normalized_rows.append(normalized)
    return tuple(normalized_rows)


def _average_numeric(values: Any, *, field_name: str) -> float:
    """Return the arithmetic mean while preserving Ragas ``NaN`` values.

    Args:
        values: Pandas Series, sequence, or scalar metric values.
        field_name: Metric name used in validation messages.

    Returns:
        Numeric average. Non-finite values are intentionally returned so the
        outer normalization step can decide whether to skip a single metric or
        fail the full evaluation when every metric is unavailable.
    """

    if hasattr(values, "mean"):
        return _numeric_float(values.mean(), field_name=field_name)
    if not isinstance(values, Sequence) or isinstance(values, str):
        return _numeric_float(values, field_name=field_name)
    if not values:
        raise ValueError(f"{field_name} must contain at least one score")
    return sum(_numeric_float(value, field_name=field_name) for value in values) / len(values)


def _normalize_metric_names(metric_names: Sequence[str]) -> tuple[str, ...]:
    """Return unique, non-blank metric names while preserving caller order."""

    normalized: list[str] = []
    seen: set[str] = set()
    for metric_name in metric_names:
        name = _required_text({"metric_name": metric_name}, "metric_name")
        if name not in seen:
            normalized.append(name)
            seen.add(name)
    if not normalized:
        raise ValueError("metric_names must contain at least one metric")
    return tuple(normalized)


def _golden_answer(record: EvaluationRecord) -> str:
    """Read the reference answer from supported golden-set field names."""

    for field_name in ("golden_answer", "ground_truth", "reference"):
        value = record.get(field_name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError("golden_answer must be a non-empty string")


def _prediction_answer(record: EvaluationRecord) -> str:
    """Read generated answer text from supported prediction field names."""

    for field_name in ("answer", "generated_answer", "response"):
        value = record.get(field_name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError("answer must be a non-empty string")


def _prediction_contexts(record: EvaluationRecord) -> list[str]:
    """Read non-empty retrieved contexts from a prediction record."""

    raw_contexts = record.get("contexts")
    if raw_contexts is None:
        raw_contexts = record.get("retrieved_contexts")
    if not isinstance(raw_contexts, Sequence) or isinstance(raw_contexts, str):
        raise ValueError("contexts must be a non-empty list of strings")
    contexts = [_required_text({"context": context}, "context") for context in raw_contexts]
    if not contexts:
        raise ValueError("contexts must be a non-empty list of strings")
    return contexts


def _required_text(record: Mapping[str, Any], field_name: str) -> str:
    """Return a stripped required text field from a mapping."""

    value = record.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _finite_float(value: Any, *, field_name: str) -> float:
    """Convert one metric value to a finite float."""

    numeric_value = _numeric_float(value, field_name=field_name)
    if not math.isfinite(numeric_value):
        raise ValueError(f"{field_name} must be finite")
    return numeric_value


def _numeric_float(value: Any, *, field_name: str) -> float:
    """Convert one metric value to a float without finite-value validation."""

    try:
        numeric_value = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must be numeric") from error
    return numeric_value
