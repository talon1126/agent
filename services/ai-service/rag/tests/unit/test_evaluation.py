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
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import src.scripts.run_evaluation as run_evaluation_module
from src.core.types import Chunk
from src.libs.evaluator import EvaluatorFactory
from src.libs.evaluator import ragas_evaluator as ragas_evaluator_module
from src.observability.evaluation import RetrievalStrategy as ExportedRetrievalStrategy
from src.observability.evaluation.metrics import HitRateMetric, MRRMetric, NDCGMetric
from src.observability.evaluation.ragas_adapter import RagasEvaluator
from src.observability.evaluation.runner import (
    EvaluationRunner,
    RetrievalStrategy,
    StrategyComparisonResult,
)
from src.scripts.run_evaluation import (
    build_prediction_from_query_result,
    select_single_collection,
)
from src.storage.repositories import EvaluationResultRecord, EvaluationRunRecord

RAG_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_SET_PATH = RAG_ROOT / "tests" / "fixtures" / "golden_set.json"
SETTINGS_PATH = RAG_ROOT / "config" / "settings.example.yaml"


@pytest.mark.unit
def test_golden_set_fixture_exists_and_contains_representative_cases() -> None:
    """Require the golden set fixture to cover all first-party knowledge collections."""

    samples = _load_golden_set()

    assert len(samples) >= 8
    assert {sample["collection"] for sample in samples} == {
        "shopping_guides",
        "faq",
        "policies",
        "manual",
    }
    joined_questions = "\n".join(str(sample["question"]) for sample in samples)
    assert "微波炉" in joined_questions
    assert "发货" in joined_questions
    assert "物流" in joined_questions
    assert "客服" in joined_questions


@pytest.mark.unit
def test_golden_set_samples_follow_required_schema() -> None:
    """Require every sample to expose fields needed by retrieval and generation metrics."""

    samples = _load_golden_set()
    sample_ids: set[str] = set()
    for sample in samples:
        sample_id = _required_non_empty_string(sample, "id")
        assert sample_id not in sample_ids
        sample_ids.add(sample_id)

        collection = _required_non_empty_string(sample, "collection")
        _required_non_empty_string(sample, "question")
        _required_non_empty_string(sample, "golden_answer")
        expected_sources = _required_non_empty_string_list(sample, "expected_sources")

        assert all(source.startswith(f"{collection}/") for source in expected_sources)
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
                    "source_path": "shopping_guides/headphones.md#wireless",
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
        run_config: Mapping[str, int] | None = None,
    ) -> dict[str, float]:
        """Record adapter input and return deterministic generation scores."""

        captured["rows"] = rows
        captured["metrics"] = metrics
        captured["run_config"] = run_config
        return {"faithfulness": 0.91, "answer_relevancy": 0.87}

    evaluator = RagasEvaluator(
        evaluate_fn=fake_ragas_evaluate,
        timeout_seconds=300,
        max_workers=8,
    )
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
    assert captured["run_config"] == {"timeout_seconds": 300, "max_workers": 8}
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


@pytest.mark.unit
def test_ragas_evaluator_skips_non_finite_metric_values() -> None:
    """Keep usable Ragas metrics when one provider metric returns NaN."""

    evaluator = RagasEvaluator(
        evaluate_fn=lambda rows, *, metrics, run_config=None: {
            "faithfulness": math.nan,
            "answer_relevancy": 0.82,
        }
    )

    scores = evaluator.evaluate(
        [{"question": "q", "golden_answer": "reference"}],
        [{"answer": "generated answer", "contexts": ["retrieved context"]}],
    )

    assert scores == {"answer_relevancy": pytest.approx(0.82)}


@pytest.mark.unit
def test_ragas_evaluator_fails_when_all_metric_values_are_non_finite() -> None:
    """Reject runs where Ragas cannot produce any finite metric."""

    evaluator = RagasEvaluator(
        evaluate_fn=lambda rows, *, metrics, run_config=None: {
            "faithfulness": math.nan,
            "answer_relevancy": math.nan,
        }
    )

    with pytest.raises(ValueError, match="no finite metrics"):
        evaluator.evaluate(
            [{"question": "q", "golden_answer": "reference"}],
            [{"answer": "generated answer", "contexts": ["retrieved context"]}],
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
def test_evaluator_factory_registers_ragas_provider_with_lazy_backend() -> None:
    """Require production evaluation orchestration to resolve the Ragas provider."""

    captured: dict[str, Any] = {}

    def fake_ragas_evaluate(
        rows: list[dict[str, Any]],
        *,
        metrics: tuple[str, ...],
        run_config: Mapping[str, int] | None = None,
    ) -> dict[str, float]:
        """Capture rows so the factory test avoids importing real Ragas."""

        captured["rows"] = rows
        captured["metrics"] = metrics
        captured["run_config"] = run_config
        return {"faithfulness": 0.93, "answer_relevancy": 0.89}

    evaluator = EvaluatorFactory.create(
        provider="ragas",
        evaluate_fn=fake_ragas_evaluate,
    )

    scores = evaluator.evaluate(
        [{"question": "q", "golden_answer": "reference"}],
        [{"answer": "generated", "contexts": ["retrieved context"]}],
    )

    assert scores == {"faithfulness": pytest.approx(0.93), "answer_relevancy": pytest.approx(0.89)}
    assert "ragas" in EvaluatorFactory.list_providers()
    assert captured["rows"][0]["ground_truth"] == "reference"
    assert captured["run_config"] == {"timeout_seconds": 300, "max_workers": 8}


@pytest.mark.unit
def test_evaluator_factory_injects_configured_ragas_model_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Require Ragas to use configured DashScope/DeepSeek providers."""

    from src.core.config import load_settings

    settings = load_settings(SETTINGS_PATH, validate_environment=False)
    llm_calls: list[dict[str, Any]] = []
    embedding_calls: list[dict[str, Any]] = []

    def fake_llm_create(**kwargs: Any) -> object:
        """Capture LLMFactory arguments without creating network clients."""

        llm_calls.append(kwargs)
        return object()

    def fake_embedding_create(**kwargs: Any) -> object:
        """Capture EmbeddingFactory arguments without creating network clients."""

        embedding_calls.append(kwargs)
        return object()

    monkeypatch.setattr(
        ragas_evaluator_module.LLMFactory,
        "create",
        fake_llm_create,
    )
    monkeypatch.setattr(
        ragas_evaluator_module.EmbeddingFactory,
        "create",
        fake_embedding_create,
    )

    evaluator = EvaluatorFactory.create(provider="ragas", settings=settings)

    assert evaluator.__class__.__name__ == "RagasEvaluatorClient"
    assert llm_calls == [
        {
            "settings": settings,
            "provider": "deepseek",
        }
    ]
    assert embedding_calls == [
        {
            "settings": settings,
            "provider": "dashscope",
        }
    ]


@pytest.mark.unit
def test_build_prediction_from_query_result_uses_ranked_chunk_text_for_contexts() -> None:
    """Convert query_result identities into the text contexts required by Ragas."""

    lookup = _FakeChunkLookup(
        [
            Chunk(
                id="chunk-b",
                text="Second ranked text.",
                chunk_index=2,
                start_offset=10,
                end_offset=29,
                metadata={},
            ),
            Chunk(
                id="chunk-a",
                text="First ranked text.",
                chunk_index=1,
                start_offset=0,
                end_offset=18,
                metadata={},
            ),
        ]
    )

    prediction = build_prediction_from_query_result(
        {
            "id": "sample-1",
            "question": "How should I choose headphones?",
            "golden_answer": "Reference answer.",
        },
        {
            "contexts": [
                {"chunk_id": "chunk-a", "score": 0.9, "rank": 1},
                {"chunk_id": "chunk-b", "score": 0.8, "rank": 2},
            ],
            "content": "Agent-ready final context.",
            "citations": [],
            "images": [],
        },
        chunk_lookup=lookup,
        query_trace_id="query-trace-1",
    )

    assert lookup.requests == [("chunk-a", "chunk-b")]
    assert prediction == {
        "sample_id": "sample-1",
        "question": "How should I choose headphones?",
        "answer": "Agent-ready final context.",
        "answer_source": "rag",
        "contexts": ["First ranked text.", "Second ranked text."],
        "retrieved_contexts": ["First ranked text.", "Second ranked text."],
        "query_trace_id": "query-trace-1",
        "context_chunk_ids": ["chunk-a", "chunk-b"],
        "sample_collection": None,
        "effective_collection": None,
    }


@pytest.mark.unit
def test_select_single_collection_rejects_mixed_golden_sets_without_filter() -> None:
    """Prevent evaluation from silently running samples against the wrong collection."""

    with pytest.raises(ValueError, match="multiple collections"):
        select_single_collection(
            [
                {"collection": "shopping_guides"},
                {"collection": "policy_faq"},
            ]
        )

    assert select_single_collection([{"collection": "shopping_guides"}]) == "shopping_guides"


@pytest.mark.unit
def test_run_evaluation_cli_passes_configured_ragas_metrics(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Require the real evaluation entrypoint to pass configured metric names."""

    settings = run_evaluation_module.load_settings(
        SETTINGS_PATH,
        validate_environment=False,
    )
    golden_set_path = tmp_path / "golden_set.json"
    golden_set_path.write_text(
        json.dumps(
            [
                {
                    "id": "sample-1",
                    "collection": "shopping_guides",
                    "question": "如何挑选微波炉？",
                    "golden_answer": "应关注容量、加热方式和售后。",
                    "expected_sources": ["shopping_guides/microwave.md"],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    captured: dict[str, Any] = {}

    class FakePool:
        """Provide the minimal pool lifecycle used by run_evaluation_cli."""

        def open(self) -> None:
            """Record that the fake pool was opened."""

            captured["pool_opened"] = True

        def close(self) -> None:
            """Record that the fake pool was closed."""

            captured["pool_closed"] = True

    class FakeRuntime:
        """Return one deterministic query execution without external services."""

        def execute(self, *args: Any, **kwargs: Any) -> Any:
            """Return a single final result and response content."""

            captured["execute_kwargs"] = kwargs
            return SimpleNamespace(
                final_results=[SimpleNamespace(chunk_id="chunk-1", score=0.91)],
                response=SimpleNamespace(
                    content="[1] 微波炉应关注容量。",
                    citations=[],
                    images=[],
                ),
            )

    class FakeChunkLookup:
        """Resolve retrieved chunk IDs into text for Ragas contexts."""

        def get_by_ids(self, chunk_ids: list[str]) -> list[Chunk]:
            """Return chunks in the requested order."""

            captured["chunk_ids"] = list(chunk_ids)
            return [
                Chunk(
                    id="chunk-1",
                    text="微波炉选购应关注容量、加热方式和售后。",
                    chunk_index=1,
                    start_offset=0,
                    end_offset=20,
                    metadata={},
                )
            ]

    class FakeEvaluationService:
        """Capture evaluator options passed by run_evaluation_cli."""

        def __init__(self, pool: Any) -> None:
            """Store the fake pool for assertion evidence."""

            captured["service_pool"] = pool

        def run_evaluation(self, **kwargs: Any) -> Any:
            """Return a successful evaluation detail while recording kwargs."""

            captured["evaluation_kwargs"] = kwargs
            return SimpleNamespace(
                run_id="eval-1",
                collection_id=kwargs["collection_id"],
                evaluator=kwargs["evaluator"],
                dataset_name=kwargs["dataset_name"],
                status="success",
                metrics={"faithfulness": 0.9},
                summary={"sample_count": 1},
                error=None,
            )

    monkeypatch.setattr(run_evaluation_module, "_load_local_environment", lambda: None)
    monkeypatch.setattr(run_evaluation_module, "load_settings", lambda: settings)
    monkeypatch.setattr(run_evaluation_module, "_create_pool", lambda database: FakePool())
    monkeypatch.setattr(run_evaluation_module, "init_schema", lambda pool: None)
    monkeypatch.setattr(run_evaluation_module, "_build_runtime", lambda *args: FakeRuntime())
    monkeypatch.setattr(
        run_evaluation_module.VectorStoreFactory,
        "create",
        lambda **kwargs: FakeChunkLookup(),
    )
    monkeypatch.setattr(run_evaluation_module, "EvaluationService", FakeEvaluationService)
    outputs: list[str] = []

    exit_code = run_evaluation_module.run_evaluation_cli(
        [
            "--collection",
            "shopping_guides",
            "--golden-set",
            str(golden_set_path),
            "--answer-source",
            "rag",
        ],
        output=outputs.append,
    )

    assert exit_code == 0
    assert captured["pool_opened"] is True
    assert captured["pool_closed"] is True
    assert captured["chunk_ids"] == ["chunk-1"]
    evaluator_options = captured["evaluation_kwargs"]["evaluator_options"]
    assert evaluator_options["settings"] is settings
    assert evaluator_options["metric_names"] == [
        "faithfulness",
        "answer_relevancy",
        "context_precision",
        "context_recall",
    ]
    assert captured["evaluation_kwargs"]["settings_snapshot"]["generation_metrics"] == [
        "faithfulness",
        "answer_relevancy",
        "context_precision",
        "context_recall",
    ]


@pytest.mark.unit
def test_run_evaluation_cli_defaults_to_message_answer_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Require official Ragas runs to evaluate stored AImodel assistant answers."""

    settings = run_evaluation_module.load_settings(
        SETTINGS_PATH,
        validate_environment=False,
    )
    golden_set_path = _write_single_sample_golden_set(tmp_path)
    captured: dict[str, Any] = {}

    _patch_evaluation_cli_dependencies(
        monkeypatch,
        settings=settings,
        captured=captured,
        message_answer=run_evaluation_module.MessageAnswer(
            message_id=88,
            conversation_id=99,
            content="这是 AImodel 最终回答。",
            query_trace_ids=("query-aimodel-1",),
        ),
    )
    outputs: list[str] = []

    exit_code = run_evaluation_module.run_evaluation_cli(
        ["--collection", "shopping_guides", "--golden-set", str(golden_set_path)],
        output=outputs.append,
    )

    assert exit_code == 0
    assert captured["aimodel_questions"] == ["如何挑选微波炉？"]
    assert captured["aimodel_requests"] == [
        {
            "question": "如何挑选微波炉？",
            "collection": "shopping_guides",
            "force_rag": True,
        }
    ]
    assert "execute_kwargs" not in captured
    assert captured["message_chat_results"] == [
        {"conversation_id": 99, "answer": "这是 AImodel 最终回答。"}
    ]
    assert captured["query_result_trace_ids"] == ["query-aimodel-1"]
    prediction = captured["evaluation_kwargs"]["predictions"][0]
    assert prediction["answer"] == "这是 AImodel 最终回答。"
    assert prediction["answer_source"] == "message"
    assert prediction["message_id"] == 88
    assert prediction["conversation_id"] == 99
    assert prediction["query_trace_id"] == "query-aimodel-1"
    assert prediction["query_trace_ids"] == ["query-aimodel-1"]
    snapshot = captured["evaluation_kwargs"]["settings_snapshot"]
    assert snapshot["answer_source"] == "message"


@pytest.mark.unit
def test_run_evaluation_cli_uses_sample_collection_when_cli_collection_is_absent(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Allow one golden set to evaluate samples from different collections."""

    settings = run_evaluation_module.load_settings(
        SETTINGS_PATH,
        validate_environment=False,
    )
    golden_set_path = tmp_path / "mixed_golden_set.json"
    golden_set_path.write_text(
        json.dumps(
            [
                {
                    "id": "sample-shopping",
                    "collection": "shopping_guides",
                    "question": "如何挑选微波炉？",
                    "golden_answer": "应关注容量、加热方式和售后。",
                },
                {
                    "id": "sample-policy",
                    "collection": "policies",
                    "question": "拒收商品后怎么处理？",
                    "golden_answer": "应按平台拒收和异常物流规则处理。",
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    captured: dict[str, Any] = {}

    _patch_evaluation_cli_dependencies(
        monkeypatch,
        settings=settings,
        captured=captured,
        message_answer=run_evaluation_module.MessageAnswer(
            message_id=88,
            conversation_id=99,
            content="这是 AImodel 最终回答。",
            query_trace_ids=("query-aimodel-1",),
        ),
    )
    outputs: list[str] = []

    exit_code = run_evaluation_module.run_evaluation_cli(
        ["--golden-set", str(golden_set_path)],
        output=outputs.append,
    )

    assert exit_code == 0
    assert captured["aimodel_requests"] == [
        {
            "question": "如何挑选微波炉？",
            "collection": "shopping_guides",
            "force_rag": True,
        },
        {
            "question": "拒收商品后怎么处理？",
            "collection": "policies",
            "force_rag": True,
        },
    ]
    assert captured["evaluation_kwargs"]["collection_id"] == "mixed"
    assert captured["evaluation_kwargs"]["settings_snapshot"]["collection"] == "mixed"
    assert captured["evaluation_kwargs"]["settings_snapshot"]["collections"] == [
        "policies",
        "shopping_guides",
    ]
    predictions = captured["evaluation_kwargs"]["predictions"]
    assert [prediction["sample_collection"] for prediction in predictions] == [
        "shopping_guides",
        "policies",
    ]
    assert [prediction["effective_collection"] for prediction in predictions] == [
        "shopping_guides",
        "policies",
    ]


@pytest.mark.unit
def test_run_evaluation_cli_supports_rag_answer_source_for_context_debugging(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep an explicit RAG-context mode for diagnosing retrieval context quality."""

    settings = run_evaluation_module.load_settings(
        SETTINGS_PATH,
        validate_environment=False,
    )
    golden_set_path = _write_single_sample_golden_set(tmp_path)
    captured: dict[str, Any] = {}

    _patch_evaluation_cli_dependencies(
        monkeypatch,
        settings=settings,
        captured=captured,
    )
    outputs: list[str] = []

    exit_code = run_evaluation_module.run_evaluation_cli(
        [
            "--collection",
            "shopping_guides",
            "--golden-set",
            str(golden_set_path),
            "--answer-source",
            "rag",
        ],
        output=outputs.append,
    )

    assert exit_code == 0
    assert captured.get("aimodel_questions") is None
    prediction = captured["evaluation_kwargs"]["predictions"][0]
    assert prediction["answer"] == "[1] 微波炉应关注容量。"
    assert prediction["answer_source"] == "rag"
    assert "message_id" not in prediction
    assert captured["evaluation_kwargs"]["settings_snapshot"]["answer_source"] == "rag"


@pytest.mark.unit
def test_run_evaluation_cli_fails_when_message_answer_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Avoid silently evaluating query_result.content when message mode is requested."""

    settings = run_evaluation_module.load_settings(
        SETTINGS_PATH,
        validate_environment=False,
    )
    golden_set_path = _write_single_sample_golden_set(tmp_path)
    captured: dict[str, Any] = {}

    _patch_evaluation_cli_dependencies(
        monkeypatch,
        settings=settings,
        captured=captured,
        message_answer=None,
    )
    errors: list[str] = []

    exit_code = run_evaluation_module.run_evaluation_cli(
        ["--collection", "shopping_guides", "--golden-set", str(golden_set_path)],
        output=lambda value: None,
        error_output=errors.append,
    )

    assert exit_code == 1
    assert "No assistant message found for AImodel conversation_id=99" in errors[0]
    assert "evaluation_kwargs" not in captured


@pytest.mark.unit
def test_run_evaluation_cli_fails_when_message_trace_link_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Require message-mode evaluation to fail when AImodel did not call RAG."""

    settings = run_evaluation_module.load_settings(
        SETTINGS_PATH,
        validate_environment=False,
    )
    golden_set_path = _write_single_sample_golden_set(tmp_path)
    captured: dict[str, Any] = {}

    _patch_evaluation_cli_dependencies(
        monkeypatch,
        settings=settings,
        captured=captured,
        message_answer=run_evaluation_module.MessageAnswer(
            message_id=88,
            conversation_id=99,
            content="这是 AImodel 最终回答。",
            query_trace_ids=(),
        ),
    )
    errors: list[str] = []

    exit_code = run_evaluation_module.run_evaluation_cli(
        ["--collection", "shopping_guides", "--golden-set", str(golden_set_path)],
        output=lambda value: None,
        error_output=errors.append,
    )

    assert exit_code == 1
    assert "No RAG query traces linked to message_id=88" in errors[0]
    assert "evaluation_kwargs" not in captured
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



def _write_single_sample_golden_set(tmp_path: Path) -> Path:
    """Create one temporary golden-set sample for evaluation CLI unit tests."""

    golden_set_path = tmp_path / "golden_set.json"
    golden_set_path.write_text(
        json.dumps(
            [
                {
                    "id": "sample-1",
                    "collection": "shopping_guides",
                    "question": "如何挑选微波炉？",
                    "golden_answer": "应关注容量、加热方式和售后。",
                    "expected_sources": ["shopping_guides/microwave.md"],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return golden_set_path


def _patch_evaluation_cli_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    *,
    settings: Any,
    captured: dict[str, Any],
    message_answer: Any = "unused",
) -> None:
    """Patch run_evaluation_cli collaborators so unit tests never hit I/O."""

    class FakePool:
        """Provide the minimal pool lifecycle used by run_evaluation_cli."""

        def open(self) -> None:
            """Record that the fake pool was opened."""

            captured["pool_opened"] = True

        def close(self) -> None:
            """Record that the fake pool was closed."""

            captured["pool_closed"] = True

    class FakeRuntime:
        """Return one deterministic query execution without external services."""

        def execute(self, *args: Any, **kwargs: Any) -> Any:
            """Return a single final result and response content."""

            captured["execute_kwargs"] = kwargs
            return SimpleNamespace(
                final_results=[SimpleNamespace(chunk_id="chunk-1", score=0.91)],
                response=SimpleNamespace(
                    content="[1] 微波炉应关注容量。",
                    citations=[],
                    images=[],
                ),
            )

    class FakeChunkLookup:
        """Resolve retrieved chunk IDs into text for Ragas contexts."""

        def get_by_ids(self, chunk_ids: list[str]) -> list[Chunk]:
            """Return chunks in the requested order."""

            captured["chunk_ids"] = list(chunk_ids)
            return [
                Chunk(
                    id="chunk-1",
                    text="微波炉选购应关注容量、加热方式和售后。",
                    chunk_index=1,
                    start_offset=0,
                    end_offset=20,
                    metadata={},
                )
            ]

    class FakeEvaluationService:
        """Capture evaluator options passed by run_evaluation_cli."""

        def __init__(self, pool: Any) -> None:
            """Store the fake pool for assertion evidence."""

            captured["service_pool"] = pool

        def run_evaluation(self, **kwargs: Any) -> Any:
            """Return a successful evaluation detail while recording kwargs."""

            captured["evaluation_kwargs"] = kwargs
            return SimpleNamespace(
                run_id="eval-1",
                collection_id=kwargs["collection_id"],
                evaluator=kwargs["evaluator"],
                dataset_name=kwargs["dataset_name"],
                status="success",
                metrics={"faithfulness": 0.9},
                summary={"sample_count": 1},
                error=None,
            )

    class FakeAImodelEvaluationClient:
        """Capture AImodel chat calls without opening HTTP connections."""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            """Store construction kwargs for configuration assertions."""

            captured["aimodel_client_kwargs"] = kwargs

        def chat(
            self,
            question: str,
            *,
            collection: str | None = None,
            force_rag: bool = False,
        ) -> dict[str, Any]:
            """Record the forced-RAG request and emulate a completed SSE chat."""

            captured.setdefault("aimodel_requests", []).append(
                {
                    "question": question,
                    "collection": collection,
                    "force_rag": force_rag,
                }
            )
            captured.setdefault("aimodel_questions", []).append(question)
            return {"conversation_id": 99, "answer": "这是 AImodel 最终回答。"}

    class FakeMessageAnswerRepository:
        """Return a configured message answer for one AImodel chat result."""

        def __init__(self, pool: Any) -> None:
            """Store the fake pool for assertion evidence."""

            captured["message_repository_pool"] = pool

        def get_answer_from_chat_result(self, chat_result: dict[str, Any]) -> Any:
            """Return the test-selected answer and record chat metadata."""

            captured.setdefault("message_chat_results", []).append(chat_result)
            return message_answer

    class FakeQueryTraceResultRepository:
        """Resolve AImodel-linked query traces without opening PostgreSQL."""

        def __init__(self, pool: Any) -> None:
            """Store the fake pool for assertion evidence."""

            captured["query_trace_repository_pool"] = pool

        def get_query_result(self, query_trace_id: str) -> dict[str, Any]:
            """Return a query_result payload for the linked trace."""

            captured.setdefault("query_result_trace_ids", []).append(query_trace_id)
            return {
                "contexts": [
                    {"chunk_id": "chunk-1", "score": 0.91, "rank": 1}
                ],
                "content": "[1] 微波炉应关注容量。",
                "citations": [],
                "images": [],
            }

    monkeypatch.setattr(run_evaluation_module, "_load_local_environment", lambda: None)
    monkeypatch.setattr(run_evaluation_module, "load_settings", lambda: settings)
    monkeypatch.setattr(run_evaluation_module, "_create_pool", lambda database: FakePool())
    monkeypatch.setattr(run_evaluation_module, "init_schema", lambda pool: None)
    monkeypatch.setattr(run_evaluation_module, "_build_runtime", lambda *args: FakeRuntime())
    monkeypatch.setattr(
        run_evaluation_module,
        "uuid4",
        lambda: SimpleNamespace(hex="fixed"),
    )
    monkeypatch.setattr(
        run_evaluation_module.VectorStoreFactory,
        "create",
        lambda **kwargs: FakeChunkLookup(),
    )
    monkeypatch.setattr(run_evaluation_module, "EvaluationService", FakeEvaluationService)
    monkeypatch.setattr(
        run_evaluation_module,
        "AImodelEvaluationClient",
        FakeAImodelEvaluationClient,
        raising=False,
    )
    monkeypatch.setattr(
        run_evaluation_module,
        "MessageAnswerRepository",
        FakeMessageAnswerRepository,
        raising=False,
    )
    monkeypatch.setattr(
        run_evaluation_module,
        "QueryTraceResultRepository",
        FakeQueryTraceResultRepository,
        raising=False,
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


class _FakeChunkLookup:
    """Return configured chunks while recording requested chunk ID order."""

    def __init__(self, chunks: list[Chunk]) -> None:
        """Store chunks in intentionally arbitrary order for order tests."""

        self._chunks = chunks
        self.requests: list[tuple[str, ...]] = []

    def get_by_ids(self, chunk_ids: list[str]) -> list[Chunk]:
        """Return existing chunks and capture the caller's ranked IDs."""

        self.requests.append(tuple(chunk_ids))
        return list(self._chunks)


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
