"""Run and read RAG evaluation data for the Dashboard evaluation panel.

``EvaluationService`` is the Dashboard-facing orchestration boundary for
quality metrics. It can execute a configured evaluator synchronously for local
development demos, persist the run and metric rows through
``EvaluationRepository``, and project historical records into compact trend
DTOs. The service does not implement metric formulas itself; concrete
evaluators remain behind ``EvaluatorFactory``.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from math import isfinite
from typing import Any
from uuid import uuid4

from src.libs.evaluator import EvaluatorFactory
from src.storage.postgres import PostgresPool
from src.storage.repositories import (
    EvaluationRepository,
    EvaluationResultRecord,
    EvaluationRunRecord,
    EvaluationSampleResultRecord,
)

EvaluationClock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class EvaluationMetricTrendPoint:
    """Represent one metric value in a Dashboard trend chart.

    The evaluation page groups these rows by ``metric_name`` so users can see
    whether retrieval or generation quality improved across strategy changes.
    It intentionally includes run identity, evaluator, dataset, status, and
    timestamp evidence so a chart point can link back to its full run detail.
    """

    run_id: str
    metric_name: str
    metric_value: float
    evaluator: str
    dataset_name: str
    status: str
    created_at: datetime | None
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class EvaluationRunSummary:
    """Represent one evaluation run in the Dashboard history table.

    Summary rows attach a compact metric map to each run. This prevents
    Streamlit page code from issuing one query per metric column or unpacking
    repository records directly while still showing enough information for a
    user to select a run for detailed inspection.
    """

    run_id: str
    collection_id: str
    evaluator: str
    dataset_name: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime | None
    metric_count: int
    metrics: Mapping[str, float] = field(default_factory=dict)
    summary: Mapping[str, Any] = field(default_factory=dict)
    error: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class EvaluationRunDetail:
    """Represent one evaluation run with all metric and settings evidence.

    Detail rows preserve the reproducibility snapshot written with the run,
    the aggregate summary, every metric value, and evaluator-specific metric
    details. Dashboard pages use this object to explain why a score changed
    without reaching back into repository dataclasses.
    """

    run_id: str
    collection_id: str
    evaluator: str
    dataset_name: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime | None
    metrics: Mapping[str, float] = field(default_factory=dict)
    metric_details: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    sample_results: tuple[EvaluationSampleResultRecord, ...] = field(default_factory=tuple)
    settings_snapshot: Mapping[str, Any] = field(default_factory=dict)
    summary: Mapping[str, Any] = field(default_factory=dict)
    error: Mapping[str, Any] | None = None


class EvaluationService:
    """Coordinate Dashboard evaluation runs, history rows, and trends."""

    def __init__(
        self,
        pool: PostgresPool,
        *,
        repository: EvaluationRepository | None = None,
        clock: EvaluationClock | None = None,
    ) -> None:
        """Bind the service to persistence and deterministic time providers.

        Args:
            pool: Open PostgreSQL pool used when ``repository`` is omitted.
            repository: Optional repository injection for tests or alternate
                persistence.
            clock: Optional callable returning an aware ``datetime``. ``None``
                uses the current UTC time.
        """

        self._repository = repository or EvaluationRepository(pool)
        self._clock = clock or (lambda: datetime.now(UTC))

    def run_evaluation(
        self,
        *,
        collection_id: str,
        evaluator: str,
        dataset_name: str,
        dataset: Sequence[Mapping[str, Any]],
        predictions: Sequence[Mapping[str, Any]],
        evaluator_options: Mapping[str, Any] | None = None,
        settings_snapshot: Mapping[str, Any] | None = None,
        run_id: str | None = None,
    ) -> EvaluationRunDetail:
        """Execute one evaluator and persist its run plus metric rows.

        Args:
            collection_id: Collection whose retrieval/generation strategy is
                being evaluated.
            evaluator: Provider name resolved through ``EvaluatorFactory``.
            dataset_name: Human-readable golden dataset identifier.
            dataset: Golden records passed to the evaluator.
            predictions: Prediction records aligned with ``dataset``.
            evaluator_options: Provider constructor options such as fake metric
                values in tests.
            settings_snapshot: Retrieval, rerank, model, or dataset settings
                captured for later comparison.
            run_id: Optional stable run ID. When omitted, a UUID-based ID is
                generated by Python.

        Returns:
            Persisted evaluation detail with metric values and metadata.

        Side Effects:
            Writes one ``rag_evaluation_runs`` row, one
            ``rag_evaluation_results`` row per aggregate metric, and one
            ``rag_evaluation_sample_results`` row per golden sample.
        """

        collection = _require_non_blank(collection_id, field_name="collection_id")
        evaluator_name = _require_non_blank(evaluator, field_name="evaluator")
        dataset_label = _require_non_blank(dataset_name, field_name="dataset_name")
        stable_run_id = (
            _require_non_blank(run_id, field_name="run_id")
            if run_id is not None
            else f"eval-{uuid4().hex}"
        )
        started_at = self._clock()
        evaluator_client = EvaluatorFactory.create(
            provider=evaluator_name,
            **dict(evaluator_options or {}),
        )
        scoreable_dataset, scoreable_predictions, scoreable_indices = _scoreable_records(
            dataset,
            predictions,
        )
        if not scoreable_predictions:
            metric_result = _EvaluationMetricResult(metrics={})
            normalized_metrics: dict[str, float] = {}
            aligned_sample_metrics = tuple({} for _ in range(len(dataset)))
        else:
            try:
                metric_result = _evaluate_with_optional_samples(
                    evaluator_client,
                    scoreable_dataset,
                    scoreable_predictions,
                )
                normalized_metrics = _normalize_metric_values(metric_result.metrics)
                aligned_sample_metrics = _align_sample_metrics(
                    sample_metrics=metric_result.sample_metrics,
                    sample_count=len(dataset),
                    scoreable_indices=scoreable_indices,
                )
            except Exception as error:
                failed_at = self._clock()
                failed_run = self._repository.upsert_run(
                    EvaluationRunRecord(
                        id=stable_run_id,
                        collection_id=collection,
                        evaluator=evaluator_name,
                        dataset_name=dataset_label,
                        status="failed",
                        started_at=started_at,
                        finished_at=failed_at,
                        settings_snapshot=dict(settings_snapshot or {}),
                        summary={"sample_count": len(dataset), "metric_count": 0},
                        error={
                            "type": error.__class__.__name__,
                            "message": str(error),
                        },
                    )
                )
                return _run_detail(failed_run, [], [])

        finished_at = self._clock()
        run = self._repository.upsert_run(
            EvaluationRunRecord(
                id=stable_run_id,
                collection_id=collection,
                evaluator=evaluator_name,
                dataset_name=dataset_label,
                status="success",
                started_at=started_at,
                finished_at=finished_at,
                settings_snapshot=dict(settings_snapshot or {}),
                summary=_evaluation_summary(
                    sample_count=len(dataset),
                    prediction_count=len(predictions),
                    metric_count=len(normalized_metrics),
                    predictions=predictions,
                    metrics=normalized_metrics,
                    aligned_sample_metrics=aligned_sample_metrics,
                ),
            )
        )
        results = self._repository.upsert_results(
            stable_run_id,
            [
                EvaluationResultRecord(
                    id=_metric_result_id(stable_run_id, metric_name),
                    run_id=stable_run_id,
                    metric_name=metric_name,
                    metric_value=metric_value,
                    details={
                        "dataset_name": dataset_label,
                        "sample_count": len(dataset),
                    },
                )
                for metric_name, metric_value in normalized_metrics.items()
            ],
        )
        sample_results = self._repository.upsert_sample_results(
            stable_run_id,
            _sample_result_records(
                stable_run_id,
                dataset,
                predictions,
                normalized_metrics,
                sample_metrics=aligned_sample_metrics,
            ),
        )
        return _run_detail(run, results, sample_results)

    def list_runs(self, collection_id: str) -> list[EvaluationRunSummary]:
        """Return evaluation run summaries newest-first for one collection.

        Args:
            collection_id: Dashboard-selected collection.

        Returns:
            Evaluation rows with metric maps already attached.
        """

        collection = _require_non_blank(collection_id, field_name="collection_id")
        summaries: list[EvaluationRunSummary] = []
        for run in self._repository.list_runs(collection):
            results = self._repository.list_results(run.id)
            summaries.append(_run_summary(run, results))
        return summaries

    def get_run_detail(self, run_id: str) -> EvaluationRunDetail | None:
        """Return one evaluation run detail with all metric rows.

        Args:
            run_id: Stable Python-generated evaluation run ID.

        Returns:
            Detail DTO, or ``None`` when no run exists.
        """

        stable_run_id = _require_non_blank(run_id, field_name="run_id")
        run = self._repository.get_run(stable_run_id)
        if run is None:
            return None
        return _run_detail(
            run,
            self._repository.list_results(stable_run_id),
            self._repository.list_sample_results(stable_run_id),
        )

    def metric_trends(
        self,
        collection_id: str,
    ) -> dict[str, list[EvaluationMetricTrendPoint]]:
        """Return metric-name grouped trend points for one collection.

        Args:
            collection_id: Dashboard-selected collection.

        Returns:
            Mapping from metric name to chronological trend points.
        """

        collection = _require_non_blank(collection_id, field_name="collection_id")
        trends: dict[str, list[EvaluationMetricTrendPoint]] = {}
        oldest_supported_timestamp = datetime.min.replace(tzinfo=UTC)
        runs = sorted(
            self._repository.list_runs(collection),
            key=lambda run: run.created_at or run.started_at or oldest_supported_timestamp,
        )
        for run in runs:
            for result in self._repository.list_results(run.id):
                trends.setdefault(result.metric_name, []).append(
                    EvaluationMetricTrendPoint(
                        run_id=run.id,
                        metric_name=result.metric_name,
                        metric_value=result.metric_value,
                        evaluator=run.evaluator,
                        dataset_name=run.dataset_name,
                        status=run.status,
                        created_at=result.created_at or run.created_at,
                        details=result.details,
                    )
                )
        return trends


@dataclass(frozen=True, slots=True)
class _EvaluationMetricResult:
    """Hold aggregate and optional per-sample evaluator metric output."""

    metrics: Mapping[str, Any]
    sample_metrics: tuple[Mapping[str, Any], ...] = ()


def _scoreable_records(
    dataset: Sequence[Mapping[str, Any]],
    predictions: Sequence[Mapping[str, Any]],
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]], list[int]]:
    """Return samples that can safely be sent to generation evaluators."""

    if len(dataset) != len(predictions):
        raise ValueError("dataset and predictions must contain the same number of records")
    scoreable_dataset: list[Mapping[str, Any]] = []
    scoreable_predictions: list[Mapping[str, Any]] = []
    scoreable_indices: list[int] = []
    for index, (sample, prediction) in enumerate(
        zip(dataset, predictions, strict=True),
    ):
        if _is_empty_prediction(prediction):
            continue
        scoreable_dataset.append(sample)
        scoreable_predictions.append(prediction)
        scoreable_indices.append(index)
    return scoreable_dataset, scoreable_predictions, scoreable_indices


def _is_empty_prediction(prediction: Mapping[str, Any]) -> bool:
    """Return whether a prediction represents a retrieval coverage miss."""

    error = prediction.get("error")
    if isinstance(error, Mapping) and error.get("empty_result") is True:
        return True
    contexts = prediction.get("retrieved_contexts")
    if contexts is None:
        contexts = prediction.get("contexts")
    return contexts == []


def _align_sample_metrics(
    *,
    sample_metrics: Sequence[Mapping[str, Any]],
    sample_count: int,
    scoreable_indices: Sequence[int],
) -> tuple[Mapping[str, Any], ...]:
    """Map evaluator sample metrics back to original dataset order."""

    if not sample_metrics:
        return tuple({} for _ in range(sample_count))
    if len(sample_metrics) != len(scoreable_indices):
        raise ValueError("sample_metrics must match scoreable sample count")
    aligned: list[Mapping[str, Any]] = [{} for _ in range(sample_count)]
    for metric_row, original_index in zip(sample_metrics, scoreable_indices, strict=True):
        aligned[original_index] = dict(metric_row)
    return tuple(aligned)


def _evaluation_summary(
    *,
    sample_count: int,
    prediction_count: int,
    metric_count: int,
    predictions: Sequence[Mapping[str, Any]],
    metrics: Mapping[str, float],
    aligned_sample_metrics: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Build run-level coverage and metric applicability summary."""

    empty_count = sum(1 for prediction in predictions if _is_empty_prediction(prediction))
    scored_by_metric: dict[str, int] = {}
    skipped_by_metric: dict[str, int] = {}
    for metric_name in metrics:
        scored_by_metric[metric_name] = sum(
            1 for row in aligned_sample_metrics if metric_name in row
        )
        skipped_by_metric[metric_name] = sample_count - scored_by_metric[metric_name]
    return {
        "sample_count": sample_count,
        "prediction_count": prediction_count,
        "metric_count": metric_count,
        "empty_sample_count": empty_count,
        "coverage_rate": 1.0 if sample_count == 0 else (sample_count - empty_count) / sample_count,
        "scored_sample_count_by_metric": scored_by_metric,
        "skipped_sample_count_by_metric": skipped_by_metric,
    }
def _evaluate_with_optional_samples(
    evaluator_client: Any,
    dataset: Sequence[Mapping[str, Any]],
    predictions: Sequence[Mapping[str, Any]],
) -> _EvaluationMetricResult:
    """Run an evaluator and preserve per-sample metric evidence when available."""

    if hasattr(evaluator_client, "evaluate_with_samples"):
        raw_result = evaluator_client.evaluate_with_samples(dataset, predictions)
    else:
        raw_result = evaluator_client.evaluate(dataset, predictions)
    if isinstance(raw_result, Mapping) and isinstance(raw_result.get("metrics"), Mapping):
        raw_sample_metrics = raw_result.get("sample_metrics") or ()
        if not isinstance(raw_sample_metrics, Sequence) or isinstance(
            raw_sample_metrics,
            (str, bytes),
        ):
            raise ValueError("sample_metrics must be a sequence of metric mappings")
        sample_metrics: list[Mapping[str, Any]] = []
        for item in raw_sample_metrics:
            if not isinstance(item, Mapping):
                raise ValueError("sample_metrics must contain metric mappings")
            sample_metrics.append(dict(item))
        if sample_metrics and len(sample_metrics) != len(dataset):
            raise ValueError("sample_metrics must match dataset sample count")
        return _EvaluationMetricResult(
            metrics=dict(raw_result["metrics"]),
            sample_metrics=tuple(sample_metrics),
        )
    if not isinstance(raw_result, Mapping):
        raise ValueError("Evaluator result must be a metric mapping")
    return _EvaluationMetricResult(metrics=dict(raw_result))


def _run_summary(
    run: EvaluationRunRecord,
    results: list[EvaluationResultRecord],
) -> EvaluationRunSummary:
    """Convert repository records into a Dashboard history row."""

    metrics = _metric_map(results)
    return EvaluationRunSummary(
        run_id=run.id,
        collection_id=run.collection_id,
        evaluator=run.evaluator,
        dataset_name=run.dataset_name,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        created_at=run.created_at,
        metric_count=len(results),
        metrics=metrics,
        summary=run.summary,
        error=run.error,
    )


def _run_detail(
    run: EvaluationRunRecord,
    results: list[EvaluationResultRecord],
    sample_results: list[EvaluationSampleResultRecord] | None = None,
) -> EvaluationRunDetail:
    """Convert repository records into a Dashboard run detail payload."""

    return EvaluationRunDetail(
        run_id=run.id,
        collection_id=run.collection_id,
        evaluator=run.evaluator,
        dataset_name=run.dataset_name,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        created_at=run.created_at,
        metrics=_metric_map(results),
        metric_details={result.metric_name: result.details for result in results},
        sample_results=tuple(sample_results or ()),
        settings_snapshot=run.settings_snapshot,
        summary=run.summary,
        error=run.error,
    )


def _sample_result_records(
    run_id: str,
    dataset: Sequence[Mapping[str, Any]],
    predictions: Sequence[Mapping[str, Any]],
    aggregate_metrics: Mapping[str, float],
    *,
    sample_metrics: Sequence[Mapping[str, Any]] = (),
) -> list[EvaluationSampleResultRecord]:
    """Build per-sample diagnostic rows from aligned dataset and predictions.

    Args:
        run_id: Parent evaluation run identifier.
        dataset: Golden-set rows passed to the evaluator.
        predictions: Generated prediction rows aligned with ``dataset``.
        aggregate_metrics: Run-level metrics persisted separately in
            ``rag_evaluation_results``. They are not copied into sample rows
            because that would misrepresent aggregate scores as per-sample
            metric evidence.

    Returns:
        One immutable sample diagnostic record per golden sample.

    Raises:
        ValueError: If dataset and prediction counts differ, or required fields
            for low-score diagnosis are missing.
    """

    if len(dataset) != len(predictions):
        raise ValueError("dataset and predictions must have the same number of samples")
    records: list[EvaluationSampleResultRecord] = []
    for index, (sample, prediction) in enumerate(
        zip(dataset, predictions, strict=True),
        start=1,
    ):
        sample_id = _sample_id(sample, index)
        metrics = prediction.get("metrics")
        if not isinstance(metrics, Mapping):
            metrics = sample_metrics[index - 1] if sample_metrics else {}
        records.append(
            EvaluationSampleResultRecord(
                id=_sample_result_id(run_id, sample_id),
                run_id=run_id,
                sample_id=sample_id,
                sample_index=index,
                collection_id=_optional_text(
                    prediction.get("effective_collection")
                    or prediction.get("sample_collection")
                    or sample.get("collection")
                ),
                question=_required_text(sample.get("question"), field_name="question"),
                golden_answer=_required_text(
                    sample.get("golden_answer") or sample.get("reference"),
                    field_name="golden_answer",
                ),
                generated_answer=_required_text(
                    prediction.get("answer"),
                    field_name="answer",
                ),
                retrieved_contexts=_text_tuple(
                    prediction.get("retrieved_contexts")
                    if prediction.get("retrieved_contexts") is not None
                    else prediction.get("contexts"),
                    field_name="retrieved_contexts",
                    allow_empty=_is_empty_prediction(prediction),
                ),
                context_chunk_ids=_text_tuple(
                    prediction.get("context_chunk_ids"),
                    field_name="context_chunk_ids",
                    allow_empty=True,
                ),
                query_trace_ids=_query_trace_ids(prediction),
                metrics=dict(metrics),
                error=_optional_mapping(prediction.get("error")),
            )
        )
    return records


def _sample_id(sample: Mapping[str, Any], index: int) -> str:
    """Return the golden sample ID or a stable index-based fallback."""

    value = sample.get("id")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return f"sample-{index}"


def _sample_result_id(run_id: str, sample_id: str) -> str:
    """Return a stable ID for one run/sample diagnostic row."""

    digest = sha256(f"{run_id}:{sample_id}".encode()).hexdigest()[:16]
    if len(run_id) + len(sample_id) < 180:
        return f"{run_id}:sample:{sample_id}"
    return f"{run_id}:sample:{digest}"


def _required_text(value: Any, *, field_name: str) -> str:
    """Return a required non-blank string for sample diagnostics."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


def _optional_text(value: Any) -> str | None:
    """Return a stripped optional string or ``None``."""

    return value.strip() if isinstance(value, str) and value.strip() else None


def _text_tuple(
    value: Any,
    *,
    field_name: str,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    """Normalize a JSON list of strings for diagnostic persistence."""

    if value is None and allow_empty:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise ValueError(f"{field_name} must be a list of strings")
    items = tuple(str(item) for item in value if str(item).strip())
    if not items and not allow_empty:
        raise ValueError(f"{field_name} must contain at least one string")
    return items


def _query_trace_ids(prediction: Mapping[str, Any]) -> tuple[str, ...]:
    """Return all trace IDs linked to a prediction in stable order."""

    raw_ids = prediction.get("query_trace_ids")
    if raw_ids is None:
        raw_ids = [prediction.get("query_trace_id")]
    return _text_tuple(raw_ids, field_name="query_trace_ids", allow_empty=True)


def _optional_mapping(value: Any) -> Mapping[str, Any] | None:
    """Return optional mapping evidence for failed sample diagnostics."""

    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("error must be an object when provided")
    return value


def _metric_map(results: list[EvaluationResultRecord]) -> dict[str, float]:
    """Return metric values keyed by stable metric name."""

    return {result.metric_name: result.metric_value for result in results}


def _normalize_metric_values(metrics: Mapping[str, Any]) -> dict[str, float]:
    """Validate evaluator metrics before marking a run successful.

    Args:
        metrics: Raw metric mapping returned by a ``BaseEvaluator``.

    Returns:
        Finite float values keyed by non-blank metric names.

    Raises:
        ValueError: If a metric name is blank or a metric value cannot be
            represented as a finite PostgreSQL ``DOUBLE PRECISION`` value.
    """

    normalized: dict[str, float] = {}
    for metric_name, metric_value in metrics.items():
        name = _require_non_blank(str(metric_name), field_name="metric_name")
        try:
            value = float(metric_value)
        except (TypeError, ValueError) as error:
            raise ValueError(f"metric {name} must be numeric") from error
        if not isfinite(value):
            raise ValueError(f"metric {name} must be finite")
        normalized[name] = value
    return normalized


def _metric_result_id(run_id: str, metric_name: str) -> str:
    """Create a deterministic metric row ID for idempotent re-runs."""

    digest = sha256(f"{run_id}:{metric_name}".encode()).hexdigest()
    return f"eval-result-{digest[:32]}"


def _require_non_blank(value: str, *, field_name: str) -> str:
    """Return a trimmed identifier or raise before repository access."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value.strip()
