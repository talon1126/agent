"""Validate evaluation fixtures and behavior contracts.

The Phase G evaluation work starts with a stable golden dataset. Later metric
and runner tasks can rely on this file without redefining the sample schema.
These tests intentionally validate the fixture as data because the first
increment is about establishing the dataset contract rather than executing an
evaluator.
"""

from __future__ import annotations

import json
import math
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from src.observability.evaluation import RetrievalStrategy as ExportedRetrievalStrategy
from src.observability.evaluation.metrics import HitRateMetric, MRRMetric, NDCGMetric
from src.observability.evaluation.ragas_adapter import RagasEvaluator
from src.observability.evaluation.runner import (
    EvaluationRunner,
    RetrievalStrategy,
    StrategyComparisonResult,
)
from src.storage.repositories import EvaluationResultRecord, EvaluationRunRecord

RAG_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_SET_PATH = RAG_ROOT / "tests" / "fixtures" / "golden_set.json"


@pytest.mark.unit
def test_golden_set_fixture_exists_and_contains_representative_cases() -> None:
    """Require the golden set fixture to exist and cover the shopping guide domain."""

    samples = _load_golden_set()

    assert len(samples) >= 3
    assert {sample["collection"] for sample in samples} == {"shopping_guides"}
    joined_questions = "\n".join(str(sample["question"]) for sample in samples)
    assert "无线耳机" in joined_questions
    assert "人体工学键盘" in joined_questions
    assert "解压玩具" in joined_questions


@pytest.mark.unit
def test_golden_set_samples_follow_required_schema() -> None:
    """Require every sample to expose fields needed by retrieval and generation metrics."""

    samples = _load_golden_set()
    sample_ids: set[str] = set()
    for sample in samples:
        sample_id = _required_non_empty_string(sample, "id")
        assert sample_id not in sample_ids
        sample_ids.add(sample_id)

        _required_non_empty_string(sample, "collection")
        _required_non_empty_string(sample, "question")
        _required_non_empty_string(sample, "golden_answer")
        expected_sources = _required_non_empty_string_list(sample, "expected_sources")
        expected_keywords = _required_non_empty_string_list(sample, "expected_keywords")

        assert all(source.startswith("shopping_guides/") for source in expected_sources)
        assert len(expected_keywords) >= 3
        assert len(str(sample["golden_answer"])) >= 30


@pytest.mark.unit
def test_custom_retrieval_metrics_score_ranked_sources_without_llm() -> None:
    """Score retrieval quality from ranked source IDs without calling any model."""

    dataset = [
        {"expected_sources": ["shopping_guides/headphones.md#wireless"]},
        {"expected_sources": ["shopping_guides/keyboards.md#ergonomic"]},
        {"expected_sources": ["shopping_guides/toys.md#stress-relief"]},
    ]
    predictions = [
        {
            "retrieved_sources": [
                "shopping_guides/other.md#noise",
                "shopping_guides/headphones.md#wireless",
                "shopping_guides/headphones.md#battery",
            ]
        },
        {
            "retrieved_sources": [
                "shopping_guides/keyboards.md#ergonomic",
                "shopping_guides/keyboards.md#layout",
            ]
        },
        {
            "retrieved_sources": [
                "shopping_guides/toys.md#office",
                "shopping_guides/toys.md#material",
                "shopping_guides/toys.md#cleaning",
                "shopping_guides/toys.md#stress-relief",
            ]
        },
    ]

    assert HitRateMetric(top_k=3).score(dataset, predictions) == pytest.approx(2 / 3)
    assert MRRMetric(top_k=3).score(dataset, predictions) == pytest.approx(
        ((1 / 2) + 1 + 0) / 3
    )
    assert NDCGMetric(top_k=3).score(dataset, predictions) == pytest.approx(
        ((1 / math.log2(3)) + 1 + 0) / 3
    )


@pytest.mark.unit
def test_custom_retrieval_metrics_accept_mapping_candidates() -> None:
    """Allow future runners to pass retrieved candidates as mapping objects."""

    dataset = [{"expected_sources": ["shopping_guides/headphones.md#wireless"]}]
    predictions = [
        {
            "retrieved_sources": [
                {"chunk_id": "chunk-1", "source": "shopping_guides/other.md#noise"},
                {
                    "chunk_id": "chunk-2",
                    "source_ref": "shopping_guides/headphones.md#wireless",
                },
            ]
        }
    ]

    assert HitRateMetric(top_k=2).score(dataset, predictions) == pytest.approx(1.0)
    assert MRRMetric(top_k=2).score(dataset, predictions) == pytest.approx(0.5)
    assert NDCGMetric(top_k=2).score(dataset, predictions) == pytest.approx(
        1 / math.log2(3)
    )


@pytest.mark.unit
def test_custom_retrieval_metrics_validate_dataset_and_prediction_contracts() -> None:
    """Fail fast when evaluation input cannot produce meaningful retrieval metrics."""

    with pytest.raises(ValueError, match="top_k must be greater than zero"):
        HitRateMetric(top_k=0)

    with pytest.raises(ValueError, match="same number"):
        MRRMetric(top_k=3).score(
            [{"expected_sources": ["shopping_guides/a.md#section"]}],
            [],
        )

    with pytest.raises(ValueError, match="expected_sources"):
        NDCGMetric(top_k=3).score(
            [{"question": "missing source contract"}],
            [{"retrieved_sources": ["shopping_guides/a.md#section"]}],
        )


@pytest.mark.unit
def test_ragas_evaluator_builds_generation_samples_with_injected_backend() -> None:
    """Verify the Ragas adapter contract without importing or calling real Ragas."""

    captured: dict[str, Any] = {}

    def fake_ragas_evaluate(
        rows: list[dict[str, Any]],
        *,
        metrics: tuple[str, ...],
    ) -> dict[str, float]:
        """Record adapter input and return deterministic generation scores."""

        captured["rows"] = rows
        captured["metrics"] = metrics
        return {"faithfulness": 0.91, "answer_relevancy": 0.87}

    evaluator = RagasEvaluator(evaluate_fn=fake_ragas_evaluate)
    dataset = [
        {
            "question": "如何挑选适合通勤的无线耳机？",
            "golden_answer": "应重点关注降噪、佩戴舒适度、续航和连接稳定性。",
        }
    ]
    predictions = [
        {
            "answer": "通勤耳机优先看主动降噪、佩戴重量、续航和蓝牙稳定性。",
            "contexts": [
                "通勤场景应优先考虑主动降噪和连接稳定性。",
                "长时间佩戴需要关注重量和耳压。",
            ],
        }
    ]

    scores = evaluator.evaluate(dataset, predictions)

    assert scores == {"faithfulness": pytest.approx(0.91), "answer_relevancy": pytest.approx(0.87)}
    assert captured["metrics"] == ("faithfulness", "answer_relevancy")
    assert captured["rows"] == [
        {
            "question": "如何挑选适合通勤的无线耳机？",
            "answer": "通勤耳机优先看主动降噪、佩戴重量、续航和蓝牙稳定性。",
            "contexts": [
                "通勤场景应优先考虑主动降噪和连接稳定性。",
                "长时间佩戴需要关注重量和耳压。",
            ],
            "ground_truth": "应重点关注降噪、佩戴舒适度、续航和连接稳定性。",
        }
    ]


@pytest.mark.unit
def test_ragas_evaluator_validates_generation_metric_contracts() -> None:
    """Fail fast when Ragas generation metrics cannot be computed safely."""

    evaluator = RagasEvaluator(evaluate_fn=lambda rows, *, metrics: {})

    with pytest.raises(ValueError, match="metric_names"):
        RagasEvaluator(metric_names=[])

    with pytest.raises(ValueError, match="same number"):
        evaluator.evaluate(
            [{"question": "q", "golden_answer": "a"}],
            [],
        )

    with pytest.raises(ValueError, match="answer"):
        evaluator.evaluate(
            [{"question": "q", "golden_answer": "a"}],
            [{"contexts": ["ctx"]}],
        )

    with pytest.raises(ValueError, match="contexts"):
        evaluator.evaluate(
            [{"question": "q", "golden_answer": "a"}],
            [{"answer": "generated", "contexts": []}],
        )


@pytest.mark.external
@pytest.mark.skipif(
    os.getenv("RUN_RAG_EXTERNAL_TESTS") != "1",
    reason="Set RUN_RAG_EXTERNAL_TESTS=1 to import optional Ragas dependencies",
)
def test_ragas_evaluator_real_backend_import_is_external_only() -> None:
    """Keep the real Ragas dependency isolated from normal unit-test execution."""

    pytest.importorskip("ragas")

    evaluator = RagasEvaluator()

    assert evaluator.metric_names == ("faithfulness", "answer_relevancy")


@pytest.mark.unit
def test_evaluation_runner_compares_default_retrieval_strategies() -> None:
    """Compare hybrid, dense-only, sparse-only, and rerank strategies deterministically."""

    calls: list[tuple[str, str, int]] = []
    dataset = [
        {
            "id": "sample-1",
            "question": "如何挑选通勤无线耳机？",
            "expected_sources": ["shopping_guides/headphones.md#wireless"],
        },
        {
            "id": "sample-2",
            "question": "人体工学键盘看什么？",
            "expected_sources": ["shopping_guides/keyboards.md#ergonomic"],
        },
    ]

    def fake_retrieval(sample: dict[str, Any], *, strategy: Any, top_k: int) -> list[str]:
        """Return ranked source IDs that make each strategy's score distinct."""

        calls.append((str(sample["id"]), strategy.name, top_k))
        results = {
            "hybrid": {
                "sample-1": ["shopping_guides/headphones.md#wireless"],
                "sample-2": ["shopping_guides/noise.md#wrong"],
            },
            "dense_only": {
                "sample-1": ["shopping_guides/noise.md#wrong"],
                "sample-2": ["shopping_guides/noise.md#wrong"],
            },
            "sparse_only": {
                "sample-1": ["shopping_guides/noise.md#wrong"],
                "sample-2": ["shopping_guides/keyboards.md#ergonomic"],
            },
            "rerank": {
                "sample-1": [
                    "shopping_guides/noise.md#wrong",
                    "shopping_guides/headphones.md#wireless",
                ],
                "sample-2": [
                    "shopping_guides/keyboards.md#ergonomic",
                    "shopping_guides/noise.md#wrong",
                ],
            },
        }
        return results[strategy.name][str(sample["id"])]

    comparison = EvaluationRunner(top_k=2).compare_strategies(
        dataset,
        retrieval_fn=fake_retrieval,
    )

    assert list(comparison) == ["hybrid", "dense_only", "sparse_only", "rerank"]
    assert comparison["hybrid"].metrics["hit_rate_at_2"] == pytest.approx(0.5)
    assert comparison["dense_only"].metrics["hit_rate_at_2"] == pytest.approx(0.0)
    assert comparison["sparse_only"].metrics["hit_rate_at_2"] == pytest.approx(0.5)
    assert comparison["rerank"].metrics["hit_rate_at_2"] == pytest.approx(1.0)
    assert comparison["rerank"].metrics["mrr_at_2"] == pytest.approx((0.5 + 1.0) / 2)
    assert comparison["rerank"].predictions[0]["retrieval_mode"] == "hybrid"
    assert comparison["rerank"].predictions[0]["use_rerank"] is True
    assert calls == [
        ("sample-1", "hybrid", 2),
        ("sample-2", "hybrid", 2),
        ("sample-1", "dense_only", 2),
        ("sample-2", "dense_only", 2),
        ("sample-1", "sparse_only", 2),
        ("sample-2", "sparse_only", 2),
        ("sample-1", "rerank", 2),
        ("sample-2", "rerank", 2),
    ]


@pytest.mark.unit
def test_evaluation_runner_validates_strategy_inputs() -> None:
    """Reject invalid comparison input before invoking a retrieval backend."""

    runner = EvaluationRunner(top_k=2)

    with pytest.raises(ValueError, match="metrics"):
        EvaluationRunner(metrics=[])

    with pytest.raises(ValueError, match="dataset"):
        runner.compare_strategies([], retrieval_fn=lambda sample, *, strategy, top_k: [])

    with pytest.raises(ValueError, match="question"):
        runner.compare_strategies(
            [{"id": "sample-1", "expected_sources": ["shopping_guides/a.md#x"]}],
            retrieval_fn=lambda sample, *, strategy, top_k: [],
        )

    with pytest.raises(ValueError, match="strategies"):
        runner.compare_strategies(
            [
                {
                    "question": "q",
                    "expected_sources": ["shopping_guides/a.md#x"],
                }
            ],
            retrieval_fn=lambda sample, *, strategy, top_k: [],
            strategies=[],
        )

    assert ExportedRetrievalStrategy is RetrievalStrategy


@pytest.mark.unit
def test_evaluation_runner_saves_strategy_results_for_dashboard_trends() -> None:
    """Persist strategy comparison results through the repository boundary."""

    repository = _FakeEvaluationRepository()
    runner = EvaluationRunner(top_k=2)
    comparison = runner.compare_strategies(
        [
            {
                "id": "sample-1",
                "question": "如何挑选通勤无线耳机？",
                "expected_sources": ["shopping_guides/headphones.md#wireless"],
            }
        ],
        retrieval_fn=lambda sample, *, strategy, top_k: [
            "shopping_guides/headphones.md#wireless"
        ],
    )

    saved = runner.save_results(
        comparison,
        repository=repository,
        collection_id="shopping_guides",
        dataset_name="golden_set",
        run_id="eval-run-test",
        now=datetime(2026, 6, 10, 12, 0, tzinfo=UTC),
        settings_snapshot={"top_k": 2},
    )

    assert saved.run.id == "eval-run-test"
    assert saved.run.status == "success"
    assert saved.run.collection_id == "shopping_guides"
    assert saved.run.dataset_name == "golden_set"
    assert saved.run.summary["strategy_count"] == 4
    assert saved.run.summary["metric_count"] == 12
    assert saved.run.summary["sample_count"] == 1
    assert saved.results == tuple(repository.result_batches[0][1])
    assert [result.metric_name for result in saved.results][:3] == [
        "hybrid.hit_rate_at_2",
        "hybrid.mrr_at_2",
        "hybrid.ndcg_at_2",
    ]
    assert saved.results[0].details["strategy"] == "hybrid"
    assert saved.results[0].details["raw_metric_name"] == "hit_rate_at_2"
    assert saved.results[0].details["predictions"][0]["sample_id"] == "sample-1"
    assert repository.run_records == [saved.run]
    assert repository.result_batches[0][0] == "eval-run-test"


@pytest.mark.unit
def test_evaluation_runner_save_results_validates_persistence_inputs() -> None:
    """Reject invalid save requests before writing evaluation history."""

    runner = EvaluationRunner(top_k=2)
    repository = _FakeEvaluationRepository()

    with pytest.raises(ValueError, match="comparison"):
        runner.save_results(
            {},
            repository=repository,
            collection_id="shopping_guides",
            dataset_name="golden_set",
        )

    with pytest.raises(ValueError, match="collection_id"):
        runner.save_results(
            {
                "hybrid": runner.compare_strategies(
                    [
                        {
                            "question": "q",
                            "expected_sources": ["shopping_guides/a.md#x"],
                        }
                    ],
                    retrieval_fn=lambda sample, *, strategy, top_k: [
                        "shopping_guides/a.md#x"
                    ],
                    strategies=[RetrievalStrategy(name="hybrid", retrieval_mode="hybrid")],
                )["hybrid"]
            },
            repository=repository,
            collection_id=" ",
            dataset_name="golden_set",
        )

    with pytest.raises(ValueError, match="prediction counts"):
        runner.save_results(
            {
                "hybrid": StrategyComparisonResult(
                    strategy=RetrievalStrategy(name="hybrid", retrieval_mode="hybrid"),
                    metrics={"hit_rate_at_2": 1.0},
                    predictions=(
                        {
                            "question": "q1",
                            "retrieved_sources": ["shopping_guides/a.md#x"],
                        },
                    ),
                ),
                "dense_only": StrategyComparisonResult(
                    strategy=RetrievalStrategy(
                        name="dense_only",
                        retrieval_mode="dense_only",
                    ),
                    metrics={"hit_rate_at_2": 1.0},
                    predictions=(),
                ),
            },
            repository=repository,
            collection_id="shopping_guides",
            dataset_name="golden_set",
        )


class _FakeEvaluationRepository:
    """Capture evaluation writes without opening PostgreSQL in unit tests."""

    def __init__(self) -> None:
        """Initialize in-memory lists that mirror repository write calls."""

        self.run_records: list[EvaluationRunRecord] = []
        self.result_batches: list[tuple[str, list[EvaluationResultRecord]]] = []

    def upsert_run(self, run: EvaluationRunRecord) -> EvaluationRunRecord:
        """Store one run record and return it like ``EvaluationRepository``."""

        self.run_records.append(run)
        return run

    def upsert_results(
        self,
        run_id: str,
        results: list[EvaluationResultRecord],
    ) -> list[EvaluationResultRecord]:
        """Store one metric batch and return it in caller order."""

        self.result_batches.append((run_id, results))
        return results


def _load_golden_set() -> list[dict[str, Any]]:
    """Load the repository golden set fixture as a JSON array."""

    with GOLDEN_SET_PATH.open(encoding="utf-8") as file:
        payload = json.load(file)
    assert isinstance(payload, list)
    assert payload
    assert all(isinstance(item, dict) for item in payload)
    return payload


def _required_non_empty_string(sample: dict[str, Any], field_name: str) -> str:
    """Return a required non-empty string field from one golden sample."""

    value = sample.get(field_name)
    assert isinstance(value, str), f"{field_name} must be a string"
    assert value.strip(), f"{field_name} must not be blank"
    return value


def _required_non_empty_string_list(
    sample: dict[str, Any],
    field_name: str,
) -> list[str]:
    """Return a required non-empty string-list field from one golden sample."""

    value = sample.get(field_name)
    assert isinstance(value, list), f"{field_name} must be a list"
    assert value, f"{field_name} must not be empty"
    assert all(isinstance(item, str) and item.strip() for item in value)
    return value
