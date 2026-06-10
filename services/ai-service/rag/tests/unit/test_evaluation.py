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
from pathlib import Path
from typing import Any

import pytest

from src.observability.evaluation.metrics import HitRateMetric, MRRMetric, NDCGMetric
from src.observability.evaluation.ragas_adapter import RagasEvaluator

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
