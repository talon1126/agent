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
invokes a supplied or lazily imported backend, and normalizes numeric metric
values.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from src.core.errors import ProviderError

EvaluationRecord = Mapping[str, Any]
DEFAULT_RAGAS_METRICS = ("faithfulness", "answer_relevancy")


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
    """

    metric_names: tuple[str, ...] = DEFAULT_RAGAS_METRICS
    evaluate_fn: RagasEvaluateFn | None = field(default=None, repr=False, compare=False)

    def __init__(
        self,
        *,
        metric_names: Sequence[str] | None = None,
        evaluate_fn: RagasEvaluateFn | None = None,
    ) -> None:
        """Create a Ragas adapter with deterministic metric-name validation.

        Args:
            metric_names: Optional ordered metric names. Blank names are
                rejected because downstream persistence uses names as stable
                metric keys.
            evaluate_fn: Optional callable replacing the real Ragas backend.
        """

        object.__setattr__(
            self,
            "metric_names",
            _normalize_metric_names(
                DEFAULT_RAGAS_METRICS if metric_names is None else metric_names
            ),
        )
        object.__setattr__(self, "evaluate_fn", evaluate_fn)

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

        rows = _build_ragas_rows(dataset, predictions)
        backend = self.evaluate_fn or _load_ragas_backend()
        try:
            raw_result = backend(rows, metrics=self.metric_names)
        except Exception as error:
            raise ProviderError(
                "Ragas evaluation failed",
                context={"metrics": list(self.metric_names), "sample_count": len(rows)},
                cause=error,
            ) from error
        return _normalize_ragas_result(raw_result, self.metric_names)


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


def _load_ragas_backend() -> RagasEvaluateFn:
    """Load the optional Ragas package only when a real evaluation runs.

    Returns:
        A callable that accepts project-normalized rows and forwards them to
        ``ragas.evaluate``.

    Raises:
        ProviderError: If optional dependencies from the ``evaluation`` extra
            are not installed.
    """

    try:
        from datasets import Dataset
        from ragas import evaluate as ragas_evaluate
        from ragas import metrics as ragas_metrics
    except ImportError as error:
        raise ProviderError(
            "Ragas is not installed. Install the evaluation extra before running "
            "real Ragas metrics.",
            context={"extra": "evaluation"},
            cause=error,
        ) from error

    def _run_ragas(rows: list[dict[str, Any]], *, metrics: tuple[str, ...]) -> Any:
        """Convert rows to a Hugging Face dataset and call ``ragas.evaluate``."""

        dataset = Dataset.from_list([_to_ragas_v02_row(row) for row in rows])
        metric_objects = [_metric_object(ragas_metrics, metric_name) for metric_name in metrics]
        return ragas_evaluate(dataset, metrics=metric_objects)

    return _run_ragas


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
        Finite float scores keyed by requested metric name.

    Raises:
        ValueError: If a required metric is missing or non-numeric.
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
    for metric_name in metric_names:
        if metric_name not in metric_values:
            raise ValueError(f"Ragas result is missing metric: {metric_name}")
        normalized[metric_name] = _finite_float(metric_values[metric_name], field_name=metric_name)
    return normalized


def _metrics_from_dataframe(dataframe: Any, metric_names: Sequence[str]) -> dict[str, float]:
    """Read metric averages from a pandas-like Ragas result dataframe."""

    values: dict[str, float] = {}
    for metric_name in metric_names:
        try:
            column = dataframe[metric_name]
        except Exception as error:
            raise ValueError(f"Ragas result is missing metric: {metric_name}") from error
        values[metric_name] = _average_numeric(column, field_name=metric_name)
    return values


def _average_numeric(values: Any, *, field_name: str) -> float:
    """Return the arithmetic mean for a pandas Series or sequence of numbers."""

    if hasattr(values, "mean"):
        return _finite_float(values.mean(), field_name=field_name)
    if not isinstance(values, Sequence) or isinstance(values, str):
        return _finite_float(values, field_name=field_name)
    if not values:
        raise ValueError(f"{field_name} must contain at least one score")
    return sum(_finite_float(value, field_name=field_name) for value in values) / len(values)


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

    try:
        numeric_value = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must be numeric") from error
    if not math.isfinite(numeric_value):
        raise ValueError(f"{field_name} must be finite")
    return numeric_value
