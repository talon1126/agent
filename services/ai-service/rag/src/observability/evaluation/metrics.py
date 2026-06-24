"""Deterministic retrieval metrics for golden-set evaluation.

The metrics in this module evaluate ranked retrieval outputs without invoking
an LLM, embedding provider, PostgreSQL, or pgvector. They intentionally operate
on plain mapping objects so the future evaluation runner can pass records
produced by HybridSearch, script fixtures, or Dashboard experiments without
introducing a dependency from observability code back into the retrieval engine.

Input contract:

- ``dataset`` is an ordered sequence of golden records.
- Each golden record must contain ``expected_sources`` as a non-empty list of
  source identifiers.
- ``predictions`` is an ordered sequence aligned with ``dataset``.
- Each prediction must contain ``retrieved_sources`` as an ordered list of
  source identifiers or candidate mappings.

Candidate mappings may expose ``source``, ``source_path``, ``id``, or
``chunk_id``. This keeps the metric layer compatible with both human-readable
source references and lower-level chunk identifiers while still failing fast
when an item cannot be normalized into a comparable string.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

EvaluationRecord = Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class HitRateMetric:
    """Compute Hit Rate@K for ranked retrieval outputs.

    Hit Rate@K measures the fraction of questions where at least one expected
    source appears in the first ``top_k`` retrieved candidates. It is useful as
    a coarse recall gate: if this value drops, the retrieval chain is failing to
    surface any relevant context before generation starts.

    Args:
        top_k: Number of ranked candidates to inspect for each prediction.
    """

    top_k: int

    def __post_init__(self) -> None:
        """Validate constructor arguments before any evaluation work starts."""

        _require_positive_top_k(self.top_k)

    @property
    def name(self) -> str:
        """Return the stable metric key stored in evaluation results."""

        return f"hit_rate_at_{self.top_k}"

    def score(
        self,
        dataset: Sequence[EvaluationRecord],
        predictions: Sequence[EvaluationRecord],
    ) -> float:
        """Return the average Hit Rate@K score.

        Args:
            dataset: Golden records containing ``expected_sources``.
            predictions: Aligned prediction records containing
                ``retrieved_sources`` in ranked order.

        Returns:
            A value in ``[0.0, 1.0]`` where ``1.0`` means every query has at
            least one relevant source in the top-k list.
        """

        pairs = _aligned_records(dataset, predictions)
        if not pairs:
            return 0.0
        hits = 0
        for golden_record, prediction_record in pairs:
            expected = _expected_sources(golden_record)
            retrieved = _retrieved_sources(prediction_record, top_k=self.top_k)
            hits += int(any(source in expected for source in retrieved))
        return hits / len(pairs)


@dataclass(frozen=True, slots=True)
class MRRMetric:
    """Compute Mean Reciprocal Rank for ranked retrieval outputs.

    MRR rewards systems that place the first relevant source earlier in the
    ranked list. A relevant source at rank 1 contributes ``1.0``; rank 2
    contributes ``0.5``; no relevant source contributes ``0.0``.

    Args:
        top_k: Optional cutoff. When provided, only the first ``top_k``
            retrieved candidates can contribute to the score.
    """

    top_k: int | None = None

    def __post_init__(self) -> None:
        """Validate the optional cutoff when one is provided."""

        if self.top_k is not None:
            _require_positive_top_k(self.top_k)

    @property
    def name(self) -> str:
        """Return the stable metric key stored in evaluation results."""

        return f"mrr_at_{self.top_k}" if self.top_k is not None else "mrr"

    def score(
        self,
        dataset: Sequence[EvaluationRecord],
        predictions: Sequence[EvaluationRecord],
    ) -> float:
        """Return the average reciprocal rank across all samples.

        Args:
            dataset: Golden records containing ``expected_sources``.
            predictions: Aligned prediction records containing ranked
                ``retrieved_sources``.

        Returns:
            Average reciprocal rank. ``0.0`` is returned for an empty dataset.
        """

        pairs = _aligned_records(dataset, predictions)
        if not pairs:
            return 0.0
        reciprocal_ranks: list[float] = []
        for golden_record, prediction_record in pairs:
            expected = _expected_sources(golden_record)
            retrieved = _retrieved_sources(prediction_record, top_k=self.top_k)
            reciprocal_ranks.append(_reciprocal_rank(expected, retrieved))
        return sum(reciprocal_ranks) / len(reciprocal_ranks)


@dataclass(frozen=True, slots=True)
class NDCGMetric:
    """Compute binary NDCG@K for ranked retrieval outputs.

    NDCG@K measures ranking quality by discounting relevant sources that appear
    lower in the list. This implementation uses binary relevance because the
    Phase G golden set declares relevant source identifiers, not graded labels.

    Args:
        top_k: Number of ranked candidates to inspect for each prediction.
    """

    top_k: int

    def __post_init__(self) -> None:
        """Validate constructor arguments before any evaluation work starts."""

        _require_positive_top_k(self.top_k)

    @property
    def name(self) -> str:
        """Return the stable metric key stored in evaluation results."""

        return f"ndcg_at_{self.top_k}"

    def score(
        self,
        dataset: Sequence[EvaluationRecord],
        predictions: Sequence[EvaluationRecord],
    ) -> float:
        """Return the average binary NDCG@K score.

        Args:
            dataset: Golden records containing ``expected_sources``.
            predictions: Aligned prediction records containing ranked
                ``retrieved_sources``.

        Returns:
            A value in ``[0.0, 1.0]`` where ``1.0`` means the retrieved ranking
            places all relevant sources in the ideal top-k order.
        """

        pairs = _aligned_records(dataset, predictions)
        if not pairs:
            return 0.0
        scores = []
        for golden_record, prediction_record in pairs:
            expected = _expected_sources(golden_record)
            retrieved = _retrieved_sources(prediction_record, top_k=self.top_k)
            scores.append(_ndcg_at_k(expected, retrieved, top_k=self.top_k))
        return sum(scores) / len(scores)


def _aligned_records(
    dataset: Sequence[EvaluationRecord],
    predictions: Sequence[EvaluationRecord],
) -> list[tuple[EvaluationRecord, EvaluationRecord]]:
    """Validate dataset/prediction alignment and return paired records.

    Args:
        dataset: Ordered golden records.
        predictions: Ordered prediction records.

    Returns:
        A list of aligned ``(golden_record, prediction_record)`` pairs.

    Raises:
        ValueError: If the two sequences do not contain the same number of
            records.
    """

    if len(dataset) != len(predictions):
        raise ValueError(
            "dataset and predictions must contain the same number of records"
        )
    return list(zip(dataset, predictions, strict=True))


def _expected_sources(record: EvaluationRecord) -> set[str]:
    """Extract and validate expected source identifiers from a golden record."""

    raw_sources = record.get("expected_sources")
    if not isinstance(raw_sources, Sequence) or isinstance(raw_sources, str):
        raise ValueError("expected_sources must be a non-empty list of strings")
    sources = {_non_blank_string(source, field_name="expected_sources") for source in raw_sources}
    if not sources:
        raise ValueError("expected_sources must be a non-empty list of strings")
    return sources


def _retrieved_sources(record: EvaluationRecord, *, top_k: int | None) -> list[str]:
    """Extract ranked retrieved source identifiers from a prediction record."""

    raw_sources = record.get("retrieved_sources")
    if raw_sources is None:
        raw_sources = record.get("sources")
    if not isinstance(raw_sources, Sequence) or isinstance(raw_sources, str):
        raise ValueError("retrieved_sources must be a list of source identifiers")

    limit = top_k if top_k is not None else len(raw_sources)
    return [_source_identifier(candidate) for candidate in raw_sources[:limit]]


def _source_identifier(candidate: Any) -> str:
    """Normalize one retrieved candidate into a comparable source identifier."""

    if isinstance(candidate, str):
        return _non_blank_string(candidate, field_name="retrieved_sources")
    if not isinstance(candidate, Mapping):
        raise ValueError("retrieved_sources items must be strings or mappings")

    for key in ("source", "source_path", "source_uri", "id", "chunk_id"):
        value = candidate.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError(
        "retrieved_sources mapping items must contain source, source_path, id, or chunk_id"
    )



def _reciprocal_rank(expected: set[str], retrieved: Sequence[str]) -> float:
    """Return reciprocal rank for the first retrieved source in ``expected``."""

    for index, source in enumerate(retrieved, start=1):
        if source in expected:
            return 1 / index
    return 0.0


def _ndcg_at_k(expected: set[str], retrieved: Sequence[str], *, top_k: int) -> float:
    """Return binary NDCG@K for one prediction row."""

    dcg = 0.0
    for index, source in enumerate(retrieved[:top_k], start=1):
        if source in expected:
            dcg += 1 / math.log2(index + 1)

    ideal_hits = min(len(expected), top_k)
    ideal_dcg = sum(1 / math.log2(index + 1) for index in range(1, ideal_hits + 1))
    if ideal_dcg == 0:
        return 0.0
    return dcg / ideal_dcg


def _non_blank_string(value: Any, *, field_name: str) -> str:
    """Return a stripped string value or raise a field-specific contract error."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must contain non-empty string values")
    return value.strip()


def _require_positive_top_k(top_k: int) -> None:
    """Reject invalid cutoffs before a metric is used."""

    if not isinstance(top_k, int) or top_k <= 0:
        raise ValueError("top_k must be greater than zero")
