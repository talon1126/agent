"""Run offline evaluation comparisons across retrieval strategies.

``EvaluationRunner`` is the Phase G orchestration layer between the golden
dataset, retrieval strategy execution, and deterministic metric calculation.
It intentionally accepts a small injected retrieval callable instead of opening
PostgreSQL, calling embeddings, or constructing ``QueryRuntime`` directly.
That boundary keeps unit tests fast and lets later scripts or Dashboard code
adapt the production query stack without changing the evaluation metric logic.

This task implements strategy comparison only. Persisting results into
PostgreSQL belongs to G5, and real query-runtime construction belongs to the
script or service layer that owns infrastructure resources.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Protocol
from uuid import uuid4

from src.observability.evaluation.metrics import HitRateMetric, MRRMetric, NDCGMetric
from src.storage.repositories import EvaluationResultRecord, EvaluationRunRecord

EvaluationRecord = Mapping[str, Any]
RetrievedCandidate = str | Mapping[str, Any]


class EvaluationAsyncLimiter:
    """Own sample and metric semaphores for async evaluation orchestration.

    The runner keeps the two limits separate because building predictions is
    mostly query/AImodel I/O, while Ragas metrics can fan out into LLM and
    embedding calls. Scripts can pass these semaphores to different layers
    without coupling sample count to provider concurrency.
    """

    def __init__(
        self,
        *,
        max_sample_concurrency: int = 1,
        max_metric_concurrency: int = 1,
    ) -> None:
        """Create bounded semaphores from validated positive limits.

        Args:
            max_sample_concurrency: Maximum golden samples building
                predictions at the same time.
            max_metric_concurrency: Maximum concurrent metric jobs allowed for
                evaluator backends that expose such a control.

        Raises:
            ValueError: If either concurrency limit is not positive.
        """

        if max_sample_concurrency <= 0:
            raise ValueError("max_sample_concurrency must be greater than zero")
        if max_metric_concurrency <= 0:
            raise ValueError("max_metric_concurrency must be greater than zero")
        self.max_sample_concurrency = max_sample_concurrency
        self.max_metric_concurrency = max_metric_concurrency
        self.sample_semaphore = asyncio.Semaphore(max_sample_concurrency)
        self.metric_semaphore = asyncio.Semaphore(max_metric_concurrency)


@dataclass(frozen=True, slots=True)
class RetrievalStrategy:
    """Describe one retrieval strategy variant used in offline comparisons.

    Args:
        name: Stable strategy key shown in evaluation output and Dashboard
            strategy comparisons.
        retrieval_mode: Retrieval route mode requested from a future production
            adapter, such as ``hybrid``, ``dense_only``, or ``sparse_only``.
        use_rerank: Whether the strategy should include the reranker stage.
    """

    name: str
    retrieval_mode: str
    use_rerank: bool = False

    def __post_init__(self) -> None:
        """Validate stable strategy identifiers at construction time."""

        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("strategy.name must be a non-empty string")
        if not isinstance(self.retrieval_mode, str) or not self.retrieval_mode.strip():
            raise ValueError("strategy.retrieval_mode must be a non-empty string")


DEFAULT_RETRIEVAL_STRATEGIES: tuple[RetrievalStrategy, ...] = (
    RetrievalStrategy(name="hybrid", retrieval_mode="hybrid", use_rerank=False),
    RetrievalStrategy(name="dense_only", retrieval_mode="dense_only", use_rerank=False),
    RetrievalStrategy(name="sparse_only", retrieval_mode="sparse_only", use_rerank=False),
    RetrievalStrategy(name="rerank", retrieval_mode="hybrid", use_rerank=True),
)


class StrategyRetrievalFn(Protocol):
    """Define the retrieval callable used by ``EvaluationRunner``.

    The callable receives one golden sample and one ``RetrievalStrategy``. It
    returns ranked source identifiers or candidate mappings compatible with the
    custom metrics from G2. This protocol is deliberately smaller than
    ``QueryRuntime`` so tests can provide simple deterministic fixtures.
    """

    def __call__(
        self,
        sample: EvaluationRecord,
        *,
        strategy: RetrievalStrategy,
        top_k: int,
    ) -> Sequence[RetrievedCandidate]:
        """Return ranked retrieval candidates for one sample and strategy."""


@dataclass(frozen=True, slots=True)
class StrategyComparisonResult:
    """Store one strategy's predictions and metric scores.

    Args:
        strategy: Retrieval strategy metadata used to produce the predictions.
        metrics: Numeric metric values such as ``hit_rate_at_10`` and
            ``mrr_at_10``.
        predictions: Prediction records passed into the metric implementations.
            They are preserved so Dashboard or tests can explain why a strategy
            received a particular score.
    """

    strategy: RetrievalStrategy
    metrics: dict[str, float]
    predictions: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class EvaluationSaveResult:
    """Return the records written for one saved evaluation comparison.

    Args:
        run: Persisted evaluation run record.
        results: Persisted metric records in repository/caller order.
    """

    run: EvaluationRunRecord
    results: tuple[EvaluationResultRecord, ...]


class EvaluationResultRepository(Protocol):
    """Describe the repository methods needed to save evaluation history."""

    def upsert_run(self, run: EvaluationRunRecord) -> EvaluationRunRecord:
        """Persist or update one evaluation run record."""

    def upsert_results(
        self,
        run_id: str,
        results: list[EvaluationResultRecord],
    ) -> list[EvaluationResultRecord]:
        """Persist or update metric rows belonging to one evaluation run."""


class EvaluationRunner:
    """Compare retrieval strategies against an ordered golden dataset."""

    def __init__(
        self,
        *,
        top_k: int = 10,
        metrics: Sequence[Any] | None = None,
    ) -> None:
        """Configure ranking cutoff and metric implementations.

        Args:
            top_k: Candidate cutoff passed to retrieval backends and metrics.
            metrics: Optional metric objects exposing ``name`` and ``score``.
                ``None`` selects Hit Rate@K, MRR@K, and NDCG@K.

        Raises:
            ValueError: If ``top_k`` is not positive or no metrics are supplied.
        """

        if not isinstance(top_k, int) or top_k <= 0:
            raise ValueError("top_k must be greater than zero")
        self._top_k = top_k
        self._metrics = tuple(_default_metrics(top_k) if metrics is None else metrics)
        if not self._metrics:
            raise ValueError("metrics must contain at least one metric")

    @property
    def top_k(self) -> int:
        """Return the ranking cutoff used by this runner."""

        return self._top_k

    def compare_strategies(
        self,
        dataset: Sequence[EvaluationRecord],
        *,
        retrieval_fn: StrategyRetrievalFn,
        strategies: Sequence[RetrievalStrategy] | None = None,
    ) -> dict[str, StrategyComparisonResult]:
        """Run each strategy over the dataset and calculate retrieval metrics.

        Args:
            dataset: Ordered golden records. Each record must contain a
                non-empty ``question`` and ``expected_doc_ids`` because strategy
                comparison evaluates retrieval quality.
            retrieval_fn: Callable that executes or simulates retrieval for one
                sample and strategy.
            strategies: Optional strategy list. ``None`` uses the four default
                Phase G variants: hybrid, dense_only, sparse_only, and rerank.

        Returns:
            Mapping from strategy name to predictions and metric scores.

        Raises:
            ValueError: If dataset, strategies, or retrieval outputs violate
                the comparison contract.
        """

        samples = _validate_dataset(dataset)
        strategy_list = _validate_strategies(
            DEFAULT_RETRIEVAL_STRATEGIES if strategies is None else strategies
        )

        comparison: dict[str, StrategyComparisonResult] = {}
        for strategy in strategy_list:
            predictions = tuple(
                self._prediction_for_sample(
                    sample,
                    strategy=strategy,
                    retrieval_fn=retrieval_fn,
                )
                for sample in samples
            )
            comparison[strategy.name] = StrategyComparisonResult(
                strategy=strategy,
                metrics=self._score(samples, predictions),
                predictions=predictions,
            )
        return comparison

    def _prediction_for_sample(
        self,
        sample: EvaluationRecord,
        *,
        strategy: RetrievalStrategy,
        retrieval_fn: StrategyRetrievalFn,
    ) -> dict[str, Any]:
        """Execute one sample/strategy pair and build a metric input record."""

        retrieved = retrieval_fn(sample, strategy=strategy, top_k=self._top_k)
        if not isinstance(retrieved, Sequence) or isinstance(retrieved, str):
            raise ValueError("retrieval_fn must return a list of retrieved candidates")
        return {
            "sample_id": sample.get("id"),
            "question": sample["question"],
            "strategy": strategy.name,
            "retrieval_mode": strategy.retrieval_mode,
            "use_rerank": strategy.use_rerank,
            "retrieved_sources": list(retrieved),
        }

    def _score(
        self,
        dataset: Sequence[EvaluationRecord],
        predictions: Sequence[EvaluationRecord],
    ) -> dict[str, float]:
        """Calculate every configured metric for one strategy's predictions."""

        scores: dict[str, float] = {}
        for metric in self._metrics:
            metric_name = _metric_name(metric)
            scores[metric_name] = float(metric.score(dataset, predictions))
        return scores

    def save_results(
        self,
        comparison: Mapping[str, StrategyComparisonResult],
        *,
        repository: EvaluationResultRepository,
        collection_id: str,
        dataset_name: str,
        evaluator: str = "strategy_comparison",
        settings_snapshot: Mapping[str, Any] | None = None,
        run_id: str | None = None,
        now: datetime | None = None,
    ) -> EvaluationSaveResult:
        """Persist strategy comparison metrics for Dashboard trend views.

        Args:
            comparison: Mapping produced by ``compare_strategies``.
            repository: Evaluation repository boundary. Production passes
                ``EvaluationRepository``; unit tests pass an in-memory fake.
            collection_id: Collection whose strategies were evaluated.
            dataset_name: Golden dataset identifier.
            evaluator: Evaluator label written to ``rag_evaluation_runs``.
            settings_snapshot: Optional reproducibility settings captured with
                the run.
            run_id: Optional stable run ID. When omitted, a UUID-based ID is
                generated by Python.
            now: Optional timestamp used for deterministic tests. ``None`` uses
                current UTC time.

        Returns:
            Run and metric records returned by the repository.

        Side Effects:
            Writes one evaluation run and one metric row per
            ``strategy.metric`` pair.
        """

        comparison_map = _validate_comparison(comparison)
        collection = _require_non_blank(collection_id, field_name="collection_id")
        dataset = _require_non_blank(dataset_name, field_name="dataset_name")
        evaluator_name = _require_non_blank(evaluator, field_name="evaluator")
        stable_run_id = (
            _require_non_blank(run_id, field_name="run_id")
            if run_id is not None
            else f"eval-{uuid4().hex}"
        )
        timestamp = now or datetime.now(UTC)
        run = repository.upsert_run(
            EvaluationRunRecord(
                id=stable_run_id,
                collection_id=collection,
                evaluator=evaluator_name,
                dataset_name=dataset,
                status="success",
                started_at=timestamp,
                finished_at=timestamp,
                settings_snapshot=dict(settings_snapshot or {}),
                summary=_comparison_summary(comparison_map),
            )
        )
        persisted_results = repository.upsert_results(
            stable_run_id,
            _comparison_result_records(
                run_id=stable_run_id,
                dataset_name=dataset,
                comparison=comparison_map,
            ),
        )
        return EvaluationSaveResult(
            run=run,
            results=tuple(persisted_results),
        )


def _default_metrics(top_k: int) -> tuple[HitRateMetric, MRRMetric, NDCGMetric]:
    """Return the default deterministic retrieval metrics for strategy comparison."""

    return (
        HitRateMetric(top_k=top_k),
        MRRMetric(top_k=top_k),
        NDCGMetric(top_k=top_k),
    )


def _validate_comparison(
    comparison: Mapping[str, StrategyComparisonResult],
) -> dict[str, StrategyComparisonResult]:
    """Validate strategy comparison output before persistence."""

    if not isinstance(comparison, Mapping) or not comparison:
        raise ValueError("comparison must contain at least one strategy result")
    normalized: dict[str, StrategyComparisonResult] = {}
    expected_prediction_count: int | None = None
    for strategy_name, result in comparison.items():
        name = _require_non_blank(strategy_name, field_name="comparison.strategy")
        if not isinstance(result, StrategyComparisonResult):
            raise ValueError("comparison values must be StrategyComparisonResult instances")
        if result.strategy.name != name:
            raise ValueError("comparison key must match result.strategy.name")
        if not result.metrics:
            raise ValueError("comparison metrics must not be empty")
        prediction_count = len(result.predictions)
        if expected_prediction_count is None:
            expected_prediction_count = prediction_count
        elif prediction_count != expected_prediction_count:
            raise ValueError("comparison prediction counts must match across strategies")
        normalized[name] = result
    return normalized


def _comparison_summary(
    comparison: Mapping[str, StrategyComparisonResult],
) -> dict[str, Any]:
    """Build run-level summary data used by Dashboard evaluation history."""

    first_result = next(iter(comparison.values()))
    return {
        "strategy_count": len(comparison),
        "metric_count": sum(len(result.metrics) for result in comparison.values()),
        "sample_count": len(first_result.predictions),
        "strategies": list(comparison),
    }


def _comparison_result_records(
    *,
    run_id: str,
    dataset_name: str,
    comparison: Mapping[str, StrategyComparisonResult],
) -> list[EvaluationResultRecord]:
    """Flatten strategy metrics into repository metric rows."""

    records: list[EvaluationResultRecord] = []
    for strategy_name, result in comparison.items():
        for metric_name, metric_value in result.metrics.items():
            full_metric_name = f"{strategy_name}.{metric_name}"
            records.append(
                EvaluationResultRecord(
                    id=_result_id(run_id, full_metric_name),
                    run_id=run_id,
                    metric_name=full_metric_name,
                    metric_value=float(metric_value),
                    details={
                        "dataset_name": dataset_name,
                        "strategy": strategy_name,
                        "retrieval_mode": result.strategy.retrieval_mode,
                        "use_rerank": result.strategy.use_rerank,
                        "raw_metric_name": metric_name,
                        "sample_count": len(result.predictions),
                        "predictions": [dict(prediction) for prediction in result.predictions],
                    },
                )
            )
    return records


def _result_id(run_id: str, metric_name: str) -> str:
    """Create a deterministic metric row ID for idempotent saves."""

    digest = sha256(f"{run_id}:{metric_name}".encode()).hexdigest()
    return f"eval-result-{digest[:32]}"


def _validate_dataset(dataset: Sequence[EvaluationRecord]) -> tuple[EvaluationRecord, ...]:
    """Validate golden records before invoking any retrieval backend."""

    if not dataset:
        raise ValueError("dataset must contain at least one sample")
    samples = tuple(dataset)
    for sample in samples:
        _require_non_blank(sample.get("question"), field_name="question")
        expected_doc_ids = sample.get("expected_doc_ids")
        if not isinstance(expected_doc_ids, Sequence) or isinstance(expected_doc_ids, str):
            raise ValueError("expected_doc_ids must be a non-empty list of strings")
        if not expected_doc_ids:
            raise ValueError("expected_doc_ids must be a non-empty list of strings")
    return samples


def _validate_strategies(
    strategies: Sequence[RetrievalStrategy],
) -> tuple[RetrievalStrategy, ...]:
    """Validate strategy uniqueness before executing comparisons."""

    if not strategies:
        raise ValueError("strategies must contain at least one strategy")
    strategy_list = tuple(strategies)
    names: set[str] = set()
    for strategy in strategy_list:
        if not isinstance(strategy, RetrievalStrategy):
            raise ValueError("strategies must contain RetrievalStrategy instances")
        if strategy.name in names:
            raise ValueError(f"duplicate strategy name: {strategy.name}")
        names.add(strategy.name)
    return strategy_list


def _metric_name(metric: Any) -> str:
    """Read the stable name from a metric object."""

    name = getattr(metric, "name", None)
    return _require_non_blank(name, field_name="metric.name")


def _require_non_blank(value: Any, *, field_name: str) -> str:
    """Return a stripped non-empty string or raise a field-specific error."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()
