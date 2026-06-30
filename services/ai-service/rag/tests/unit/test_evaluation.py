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
from src.libs.evaluator.ragas_evaluator import RagasEvaluatorClient
from src.observability.evaluation import RetrievalStrategy as ExportedRetrievalStrategy
from src.observability.evaluation import ragas_adapter as ragas_adapter_module
from src.observability.evaluation.metrics import HitRateMetric, MRRMetric, NDCGMetric
from src.observability.evaluation.ragas_adapter import RagasEvaluator
from src.observability.evaluation.runner import (
    EvaluationRunner,
    RetrievalStrategy,
    StrategyComparisonResult,
)
from src.observability.services.evaluation_service import EvaluationService
from src.scripts.run_evaluation import (
    build_prediction_from_query_result,
    select_single_collection,
)
from src.storage.repositories import (
    EvaluationResultRecord,
    EvaluationRunRecord,
    EvaluationSampleResultRecord,
)

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
        expected_doc_ids = _required_non_empty_string_list(sample, "expected_doc_ids")

        assert all(source.startswith(f"{collection}/") for source in expected_doc_ids)
        assert len(str(sample["golden_answer"])) >= 30


@pytest.mark.unit
def test_custom_retrieval_metrics_score_ranked_sources_without_llm() -> None:
    """Score retrieval quality from ranked source IDs without calling any model."""

    dataset = [
        {"expected_doc_ids": ["shopping_guides/headphones.md#wireless"]},
        {"expected_doc_ids": ["shopping_guides/keyboards.md#ergonomic"]},
        {"expected_doc_ids": ["shopping_guides/toys.md#stress-relief"]},
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

    dataset = [{"expected_doc_ids": ["shopping_guides/headphones.md#wireless"]}]
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
            [{"expected_doc_ids": ["shopping_guides/a.md#section"]}],
            [],
        )

    with pytest.raises(ValueError, match="expected_doc_ids"):
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
def test_ragas_evaluator_returns_dataframe_sample_metrics() -> None:
    """Extract per-sample Ragas scores from dataframe-backed results."""

    class FakeSeries(list[float]):
        """Provide the pandas Series mean method used by the adapter."""

        def mean(self) -> float:
            """Return the arithmetic mean of the fake column."""

            return sum(self) / len(self)

    class FakeDataFrame:
        """Expose the minimal pandas DataFrame behavior used by the adapter."""

        def __init__(self) -> None:
            """Store deterministic row-level metric values."""

            self._rows = [
                {"faithfulness": 0.9, "answer_relevancy": 0.8},
                {"faithfulness": 0.6, "answer_relevancy": 0.7},
            ]

        def __getitem__(self, key: str) -> FakeSeries:
            """Return one fake metric column by name."""

            return FakeSeries([row[key] for row in self._rows])

        def to_dict(self, orient: str) -> list[dict[str, float]]:
            """Return row dictionaries for ``orient=records``."""

            assert orient == "records"
            return list(self._rows)

    class FakeRagasResult:
        """Expose a dataframe-backed Ragas result shape."""

        def to_pandas(self) -> FakeDataFrame:
            """Return the fake dataframe used by adapter normalization."""

            return FakeDataFrame()

    evaluator = RagasEvaluator(
        evaluate_fn=lambda rows, *, metrics, run_config=None: FakeRagasResult()
    )

    result = evaluator.evaluate_with_samples(
        [
            {"question": "q1", "golden_answer": "reference 1"},
            {"question": "q2", "golden_answer": "reference 2"},
        ],
        [
            {"answer": "answer 1", "contexts": ["context 1"]},
            {"answer": "answer 2", "contexts": ["context 2"]},
        ],
    )

    assert result["metrics"] == {
        "faithfulness": pytest.approx(0.75),
        "answer_relevancy": pytest.approx(0.75),
    }
    assert result["sample_metrics"] == (
        {"faithfulness": 0.9, "answer_relevancy": 0.8},
        {"faithfulness": 0.6, "answer_relevancy": 0.7},
    )


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
def test_build_prediction_from_query_result_preserves_empty_result_diagnostics() -> None:
    """Represent Self-RAG empty fallback as a diagnosable prediction row."""

    lookup = _FakeChunkLookup([])

    prediction = build_prediction_from_query_result(
        {
            "id": "sample-empty",
            "collection": "faq",
            "question": "空调开机有异味怎么办？",
            "golden_answer": "应检查滤网、蒸发器和排水区域。",
        },
        {
            "contexts": [],
            "content": "",
            "citations": [],
            "images": [],
            "is_empty": True,
            "empty_reason": "self_rag_low_confidence",
        },
        chunk_lookup=lookup,
        query_trace_id="query-trace-empty",
        effective_collection="faq",
    )

    assert lookup.requests == []
    assert prediction["answer"] == "未检索到足够可信的内部知识。"
    assert prediction["contexts"] == []
    assert prediction["retrieved_contexts"] == []
    assert prediction["context_chunk_ids"] == []
    assert prediction["error"] == {
        "empty_result": True,
        "empty_reason": "self_rag_low_confidence",
        "skipped_metrics": [
            "faithfulness",
            "answer_relevancy",
            "context_precision",
            "context_recall",
        ],
        "scored_metrics": [],
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
                    "expected_doc_ids": ["shopping_guides/microwave.md"],
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
                run_id=kwargs["run_id"],
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
    monkeypatch.setattr(
        run_evaluation_module,
        "_build_evaluation_runtime",
        lambda *args: FakeRuntime(),
    )
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
        {"question": "如何挑选微波炉？"},
        {"question": "拒收商品后怎么处理？"},
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
def test_run_evaluation_passes_sample_collections_to_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Route multi-collection golden samples through runtime collection override."""

    golden_set_path = tmp_path / "golden_set_multi_collection.json"
    golden_set_path.write_text(
        json.dumps(
            [
                {
                    "id": "multi-1",
                    "collection": "manual",
                    "collections": ["manual", "policies"],
                    "question": "退款慢时客服怎么解释？",
                    "golden_answer": "客服应安抚并解释退款时效。",
                    "expected_doc_ids": [
                        "manual/客服话术手册.md",
                        "policies/售后服务政策.md",
                    ],
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    captured: dict[str, Any] = {}
    settings = run_evaluation_module.load_settings(
        SETTINGS_PATH,
        validate_environment=False,
    )
    settings.evaluation.answer_source = "rag"
    settings.evaluation.async_enabled = True
    settings.retrieval.async_enabled = True
    _patch_evaluation_cli_dependencies(
        monkeypatch,
        settings=settings,
        captured=captured,
    )

    exit_code = run_evaluation_module.run_evaluation_cli(
        ["--golden-set", str(golden_set_path)]
    )

    assert exit_code == 0
    assert captured["execute_kwargs"]["collection"] == "manual"
    assert captured["execute_kwargs"]["collections"] == ("manual", "policies")

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
def test_evaluation_reporter_writes_console_progress_and_jsonl_events(
    tmp_path: Path,
) -> None:
    """Require evaluation progress to be visible without corrupting final JSON output."""

    log_path = tmp_path / "evaluation.log.jsonl"
    console_lines: list[str] = []
    clock_values = iter([10.0, 10.5, 11.0, 11.5, 12.0, 12.5, 13.0, 13.5, 14.0, 14.5, 15.0, 15.5])
    reporter = run_evaluation_module.EvaluationReporter(
        console=console_lines.append,
        log_path=log_path,
        clock=lambda: next(clock_values),
    )

    reporter.run_started(
        run_id="eval-test",
        dataset_name="golden_set",
        sample_count=2,
        answer_source="message",
        top_k=5,
        no_rerank=False,
        evaluator="ragas",
        metric_names=["faithfulness", "answer_relevancy"],
        collections=["faq", "shopping_guides"],
    )
    reporter.step_started("build_predictions")
    reporter.sample_started(
        sample_index=1,
        sample_count=2,
        sample={
            "id": "sample-1",
            "collection": "faq",
            "question": "微波炉售后政策是什么？",
        },
    )
    reporter.sample_step_done(
        sample_index=1,
        sample={"id": "sample-1", "collection": "faq"},
        step="aimodel_chat",
        status="success",
        details={"message_id": 88, "query_trace_ids": ["query-1"]},
    )
    reporter.step_done("build_predictions")

    assert any("[eval-test] started" in line for line in console_lines)
    assert any("[0s] [eval-test] build_predictions started" in line for line in console_lines)
    assert any("sample-1 aimodel_chat success message_id=88" in line for line in console_lines)
    completed_line = next(line for line in console_lines if "build_predictions completed" in line)
    assert "duration=" not in completed_line
    sample_line = next(line for line in console_lines if "[1/2] sample-1" in line)
    assert "question_chars=11" in sample_line
    assert "微波炉" not in sample_line
    events = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [event["event"] for event in events] == [
        "run_started",
        "step_started",
        "sample_started",
        "sample_step_done",
        "step_done",
    ]
    assert all(event["timestamp"].endswith("+08:00") for event in events)
    assert events[0]["dataset_name"] == "golden_set"
    assert events[0]["sample_count"] == 2
    assert events[0]["answer_source"] == "message"
    assert events[2]["sample_index"] == 1
    assert events[2]["collection"] == "faq"
    assert events[2]["question_preview"] == "微波炉售后政策是什么？"
    assert events[3]["step"] == "aimodel_chat"
    assert events[3]["message_id"] == 88
    assert events[4]["duration_ms"] == pytest.approx(3000.0)


@pytest.mark.unit
def test_run_evaluation_cli_reports_sample_progress_without_changing_final_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Keep stdout machine-readable while stderr exposes long-running progress."""

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
    log_path = tmp_path / "evaluation.log.jsonl"
    monkeypatch.setattr(
        run_evaluation_module,
        "EVALUATION_LOG_PATH",
        log_path,
        raising=False,
    )
    outputs: list[str] = []
    progress_lines: list[str] = []

    exit_code = run_evaluation_module.run_evaluation_cli(
        ["--collection", "shopping_guides", "--golden-set", str(golden_set_path)],
        output=outputs.append,
        error_output=progress_lines.append,
    )

    assert exit_code == 0
    assert len(outputs) == 1
    final_payload = json.loads(outputs[0])
    assert final_payload["run_id"] == captured["evaluation_kwargs"]["run_id"]
    assert any("build_predictions started" in line for line in progress_lines)
    assert any("[1/1] sample-1" in line for line in progress_lines)
    assert any("aimodel_chat success" in line for line in progress_lines)
    assert any("message_resolve success" in line for line in progress_lines)
    assert any("query_trace_load success" in line for line in progress_lines)
    assert any("prediction_ready success" in line for line in progress_lines)
    assert any("ragas_evaluation completed" in line for line in progress_lines)
    events = [
        json.loads(line)
        for line in log_path.read_text(encoding="utf-8").splitlines()
    ]
    assert "sample_started" in [event["event"] for event in events]
    assert {
        event["step"]
        for event in events
        if event["event"] == "sample_step_done"
    } >= {"aimodel_chat", "message_resolve", "query_trace_load", "prediction_ready"}

@pytest.mark.unit
def test_evaluation_reporter_formats_elapsed_time_and_refreshes_single_line(
    tmp_path: Path,
) -> None:
    """Require the console UI to use installer-style elapsed time refreshes."""

    writes: list[str] = []
    reporter = run_evaluation_module.EvaluationReporter(
        console=writes.append,
        log_path=tmp_path / "evaluation.log.jsonl",
        clock=lambda: 0.0,
        refresh=True,
    )

    assert reporter.format_elapsed(7) == "7s"
    assert reporter.format_elapsed(72) == "1m12s"
    assert reporter.format_elapsed(3661) == "1h01m01s"

    reporter.run_started(
        run_id="eval-refresh",
        dataset_name="golden_set",
        sample_count=1,
        answer_source="message",
        top_k=5,
        no_rerank=False,
        evaluator="ragas",
        metric_names=["faithfulness"],
        collections=["faq"],
    )
    reporter.step_started("ragas_evaluation")
    reporter.render_status("ragas_evaluation running llm_calls=12 embedding_calls=4")

    assert writes[-1].startswith("\r[0s] ragas_evaluation running")
    assert not writes[-1].endswith("\n")


@pytest.mark.unit
def test_ragas_adapter_reports_llm_and_embedding_model_calls() -> None:
    """Expose Ragas model-call progress without logging prompts or vectors."""

    events: list[dict[str, Any]] = []
    llm = _ObservedFakeLLM()
    embedding = _ObservedFakeEmbedding()
    backend = ragas_adapter_module._load_ragas_backend_for_test(
        llm_client=llm,
        embedding_client=embedding,
        observer=events.append,
        timeout_seconds=300,
        max_workers=8,
    )

    result = backend(
        [
            {
                "question": "q",
                "answer": "a",
                "contexts": ["context one", "context two"],
                "ground_truth": "reference",
            }
        ],
        metrics=("faithfulness", "answer_relevancy"),
        run_config={"timeout_seconds": 300, "max_workers": 8},
    )

    assert result == {"faithfulness": 1.0, "answer_relevancy": 1.0}
    event_names = [event["event"] for event in events]
    assert "ragas_llm_call_started" in event_names
    assert "ragas_llm_call_done" in event_names
    assert "ragas_embedding_call_started" in event_names
    assert "ragas_embedding_call_done" in event_names
    llm_done = next(event for event in events if event["event"] == "ragas_llm_call_done")
    assert llm_done["provider"] == "fake"
    assert llm_done["model"] == "fake-eval"
    assert llm_done["output_chars"] == len("faithful answer")
    embedding_done = next(
        event for event in events if event["event"] == "ragas_embedding_call_done"
    )
    assert embedding_done["method"] == "embed_documents"
    assert embedding_done["vector_count"] == 2
    assert embedding_done["dimension"] == 3
    assert all("prompt" not in event for event in events)
    assert all("vectors" not in event for event in events)


@pytest.mark.unit
def test_ragas_status_text_expands_llm_and_embedding_details() -> None:
    """Show useful Ragas model-call details in console progress."""

    llm_started = run_evaluation_module._ragas_status_text(
        {
            "step": "ragas_llm",
            "status": "started",
            "call_id": "llm-1",
            "prompt_chars": 4307,
            "n": 1,
            "temperature": 0.01,
            "has_stop": False,
        }
    )
    assert llm_started == (
        "ragas_llm started call_id=llm-1 prompt_chars=4307 n=1 "
        "temperature=0.01 has_stop=False"
    )

    llm_done = run_evaluation_module._ragas_status_text(
        {
            "step": "ragas_llm",
            "status": "success",
            "call_id": "llm-1",
            "duration_ms": 7562.0,
            "provider": "ccswitch",
            "model": "gpt-5.5",
            "output_chars": 138,
        }
    )
    assert llm_done == (
        "ragas_llm success call_id=llm-1 duration=7.6s "
        "provider=ccswitch model=gpt-5.5 output_chars=138"
    )

    embedding_started = run_evaluation_module._ragas_status_text(
        {
            "step": "ragas_embedding",
            "status": "started",
            "call_id": "emb-1",
            "method": "embed_documents",
            "text_count": 3,
            "total_chars": 153,
        }
    )
    assert embedding_started == (
        "ragas_embedding started call_id=emb-1 method=embed_documents "
        "text_count=3 total_chars=153"
    )

    embedding_done = run_evaluation_module._ragas_status_text(
        {
            "step": "ragas_embedding",
            "status": "success",
            "call_id": "emb-1",
            "method": "embed_documents",
            "duration_ms": 218.0,
            "vector_count": 3,
            "dimension": 1536,
        }
    )
    assert embedding_done == (
        "ragas_embedding success call_id=emb-1 method=embed_documents "
        "duration=0.2s vector_count=3 dimension=1536"
    )

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
    assert any(
        "No assistant message found for AImodel conversation_id=99" in line
        for line in errors
    )
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
    assert any("No RAG query traces linked to message_id=88" in line for line in errors)
    assert "evaluation_kwargs" not in captured
@pytest.mark.unit
def test_evaluation_runner_compares_default_retrieval_strategies() -> None:
    """Compare hybrid, dense-only, sparse-only, and rerank strategies deterministically."""

    calls: list[tuple[str, str, int]] = []
    dataset = [
        {
            "id": "sample-1",
            "question": "如何挑选通勤无线耳机？",
            "expected_doc_ids": ["shopping_guides/headphones.md#wireless"],
        },
        {
            "id": "sample-2",
            "question": "人体工学键盘看什么？",
            "expected_doc_ids": ["shopping_guides/keyboards.md#ergonomic"],
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
            [{"id": "sample-1", "expected_doc_ids": ["shopping_guides/a.md#x"]}],
            retrieval_fn=lambda sample, *, strategy, top_k: [],
        )

    with pytest.raises(ValueError, match="strategies"):
        runner.compare_strategies(
            [
                {
                    "question": "q",
                    "expected_doc_ids": ["shopping_guides/a.md#x"],
                }
            ],
            retrieval_fn=lambda sample, *, strategy, top_k: [],
            strategies=[],
        )

    assert ExportedRetrievalStrategy is RetrievalStrategy


@pytest.mark.unit
def test_evaluation_service_persists_sample_result_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Store per-golden-sample answers, contexts, trace IDs, and metrics."""

    class FakeEvaluator:
        """Return aggregate metrics while exposing per-sample metric evidence."""

        def evaluate(
            self,
            dataset: list[Mapping[str, Any]],
            predictions: list[Mapping[str, Any]],
        ) -> dict[str, float]:
            """Validate aligned inputs and return deterministic aggregate scores."""

            assert len(dataset) == 2
            assert len(predictions) == 2
            return {"faithfulness": 0.75, "answer_relevancy": 0.8}

    repository = _FakeEvaluationRepository()
    monkeypatch.setattr(
        EvaluatorFactory,
        "create",
        lambda **kwargs: FakeEvaluator(),
    )
    service = EvaluationService(
        SimpleNamespace(),
        repository=repository,
        clock=lambda: datetime(2026, 6, 27, 12, 0, tzinfo=UTC),
    )
    dataset = [
        {
            "id": "sample-1",
            "collection": "faq",
            "question": "金属碗能放微波炉吗？",
            "golden_answer": "普通家庭不建议把金属碗放入微波炉。",
        },
        {
            "id": "sample-2",
            "collection": "manual",
            "question": "物流异常时客服如何安抚？",
            "golden_answer": "客服应先安抚，再说明催查和补发退款流程。",
        },
    ]
    predictions = [
        {
            "answer": "不建议将金属碗放入微波炉。",
            "contexts": ["金属会反射微波并产生火花。"],
            "context_chunk_ids": ["chunk-1"],
            "query_trace_id": "trace-1",
            "query_trace_ids": ["trace-1"],
            "sample_collection": "faq",
            "effective_collection": "faq",
            "message_id": 88,
            "conversation_id": 99,
            "metrics": {"faithfulness": 0.95},
        },
        {
            "answer": "我理解您着急，会帮您核查物流。",
            "retrieved_contexts": ["物流异常应先安抚用户并发起催查。"],
            "context_chunk_ids": ["chunk-2"],
            "query_trace_ids": ["trace-2", "trace-3"],
            "sample_collection": "manual",
            "effective_collection": "manual",
            "metrics": {"faithfulness": 0.55, "answer_relevancy": 0.7},
        },
    ]

    detail = service.run_evaluation(
        collection_id="mixed",
        evaluator="fake",
        dataset_name="golden_set",
        dataset=dataset,
        predictions=predictions,
        run_id="eval-samples",
    )

    assert detail.metrics == {"faithfulness": 0.75, "answer_relevancy": 0.8}
    assert repository.sample_result_batches[0][0] == "eval-samples"
    sample_results = repository.sample_result_batches[0][1]
    assert [result.sample_id for result in sample_results] == ["sample-1", "sample-2"]
    assert sample_results[0] == EvaluationSampleResultRecord(
        id="eval-samples:sample:sample-1",
        run_id="eval-samples",
        sample_id="sample-1",
        sample_index=1,
        collection_id="faq",
        question="金属碗能放微波炉吗？",
        golden_answer="普通家庭不建议把金属碗放入微波炉。",
        generated_answer="不建议将金属碗放入微波炉。",
        retrieved_contexts=("金属会反射微波并产生火花。",),
        context_chunk_ids=("chunk-1",),
        query_trace_ids=("trace-1",),
        metrics={"faithfulness": 0.95},
        error=None,
    )
    assert sample_results[1].retrieved_contexts == (
        "物流异常应先安抚用户并发起催查。",
    )
    assert sample_results[1].metrics == {"faithfulness": 0.55, "answer_relevancy": 0.7}


@pytest.mark.unit
def test_evaluation_service_persists_evaluator_sample_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persist true per-sample metrics returned by the evaluator backend."""

    class FakeEvaluator:
        """Return aggregate metrics plus aligned per-sample metric evidence."""

        def evaluate(
            self,
            dataset: list[Mapping[str, Any]],
            predictions: list[Mapping[str, Any]],
        ) -> Mapping[str, Any]:
            """Return a result shape containing sample_metrics."""

            assert len(dataset) == 2
            assert len(predictions) == 2
            return {
                "metrics": {"faithfulness": 0.75, "answer_relevancy": 0.8},
                "sample_metrics": [
                    {"faithfulness": 0.9, "answer_relevancy": 0.8},
                    {"faithfulness": 0.6, "answer_relevancy": 0.7},
                ],
            }

    repository = _FakeEvaluationRepository()
    monkeypatch.setattr(EvaluatorFactory, "create", lambda **kwargs: FakeEvaluator())
    service = EvaluationService(
        SimpleNamespace(),
        repository=repository,
        clock=lambda: datetime(2026, 6, 27, 12, 0, tzinfo=UTC),
    )
    dataset = [
        {
            "id": "sample-1",
            "collection": "faq",
            "question": "金属碗能放微波炉吗？",
            "golden_answer": "普通家庭不建议把金属碗放入微波炉。",
        },
        {
            "id": "sample-2",
            "collection": "manual",
            "question": "物流异常怎么安抚？",
            "golden_answer": "客服应先安抚并发起物流催查。",
        },
    ]
    predictions = [
        {
            "answer": "不建议将金属碗放入微波炉。",
            "retrieved_contexts": ["金属会反射微波并产生火花。"],
            "context_chunk_ids": ["chunk-1"],
            "query_trace_id": "trace-1",
            "effective_collection": "faq",
        },
        {
            "answer": "先安抚用户，再发起物流催查。",
            "retrieved_contexts": ["物流异常应先安抚用户并发起催查。"],
            "context_chunk_ids": ["chunk-2"],
            "query_trace_ids": ["trace-2"],
            "effective_collection": "manual",
        },
    ]

    service.run_evaluation(
        collection_id="mixed",
        evaluator="ragas",
        dataset_name="golden_set",
        dataset=dataset,
        predictions=predictions,
        run_id="eval-sample-metrics",
    )

    sample_results = repository.sample_result_batches[0][1]
    assert [result.metrics for result in sample_results] == [
        {"faithfulness": 0.9, "answer_relevancy": 0.8},
        {"faithfulness": 0.6, "answer_relevancy": 0.7},
    ]


@pytest.mark.unit
def test_evaluation_service_does_not_copy_aggregate_metrics_to_samples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sample rows must not pretend aggregate run metrics are per-sample scores."""

    class FakeEvaluator:
        """Return aggregate metrics only, with no per-sample evidence."""

        def evaluate(
            self,
            dataset: list[Mapping[str, Any]],
            predictions: list[Mapping[str, Any]],
        ) -> dict[str, float]:
            """Return run-level metrics for the full dataset."""

            assert len(dataset) == 2
            assert len(predictions) == 2
            return {"faithfulness": 0.75, "answer_relevancy": 0.8}

    repository = _FakeEvaluationRepository()
    monkeypatch.setattr(EvaluatorFactory, "create", lambda **kwargs: FakeEvaluator())
    service = EvaluationService(
        SimpleNamespace(),
        repository=repository,
        clock=lambda: datetime(2026, 6, 27, 12, 0, tzinfo=UTC),
    )
    dataset = [
        {
            "id": "sample-1",
            "collection": "faq",
            "question": "金属碗能放微波炉吗？",
            "golden_answer": "普通家庭不建议把金属碗放入微波炉。",
        },
        {
            "id": "sample-2",
            "collection": "manual",
            "question": "物流异常时客服如何安抚？",
            "golden_answer": "客服应先安抚，再说明催查和补发退款流程。",
        },
    ]
    predictions = [
        {
            "answer": "不建议将金属碗放入微波炉。",
            "contexts": ["金属会反射微波并产生火花。"],
            "context_chunk_ids": ["chunk-1"],
            "query_trace_id": "trace-1",
        },
        {
            "answer": "我理解您着急，会帮您核查物流。",
            "contexts": ["物流异常应先安抚用户并发起催查。"],
            "context_chunk_ids": ["chunk-2"],
            "query_trace_id": "trace-2",
        },
    ]

    service.run_evaluation(
        collection_id="mixed",
        evaluator="fake",
        dataset_name="golden_set",
        dataset=dataset,
        predictions=predictions,
        run_id="eval-no-sample-metrics",
    )

    sample_results = repository.sample_result_batches[0][1]
    assert [result.metrics for result in sample_results] == [{}, {}]

@pytest.mark.unit
def test_evaluation_service_skips_empty_predictions_for_ragas_and_persists_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Empty RAG samples should reduce coverage, not fail the whole run."""

    class FakeEvaluator:
        """Require that only scoreable rows reach the evaluator."""

        def evaluate_with_samples(
            self,
            dataset: list[Mapping[str, Any]],
            predictions: list[Mapping[str, Any]],
        ) -> Mapping[str, Any]:
            """Return metrics for the single non-empty prediction."""

            assert [sample["id"] for sample in dataset] == ["sample-ok"]
            assert [prediction["sample_id"] for prediction in predictions] == ["sample-ok"]
            return {
                "metrics": {"faithfulness": 0.8, "answer_relevancy": 0.75},
                "sample_metrics": [
                    {"faithfulness": 0.8, "answer_relevancy": 0.75},
                ],
            }

    repository = _FakeEvaluationRepository()
    monkeypatch.setattr(EvaluatorFactory, "create", lambda **kwargs: FakeEvaluator())
    service = EvaluationService(
        SimpleNamespace(),
        repository=repository,
        clock=lambda: datetime(2026, 6, 27, 12, 0, tzinfo=UTC),
    )
    dataset = [
        {
            "id": "sample-ok",
            "collection": "faq",
            "question": "金属碗能放微波炉吗？",
            "golden_answer": "普通家庭不建议把金属碗放入微波炉。",
        },
        {
            "id": "sample-empty",
            "collection": "faq",
            "question": "空调开机有异味怎么办？",
            "golden_answer": "应检查滤网、蒸发器和排水区域。",
        },
    ]
    predictions = [
        {
            "sample_id": "sample-ok",
            "answer": "不建议将金属碗放入微波炉。",
            "contexts": ["金属会反射微波并产生火花。"],
            "context_chunk_ids": ["chunk-1"],
            "query_trace_id": "trace-1",
            "effective_collection": "faq",
        },
        {
            "sample_id": "sample-empty",
            "answer": "未检索到足够可信的内部知识。",
            "contexts": [],
            "retrieved_contexts": [],
            "context_chunk_ids": [],
            "query_trace_id": "trace-empty",
            "effective_collection": "faq",
            "error": {
                "empty_result": True,
                "empty_reason": "self_rag_low_confidence",
                "skipped_metrics": ["faithfulness", "answer_relevancy"],
                "scored_metrics": [],
            },
        },
    ]

    detail = service.run_evaluation(
        collection_id="faq",
        evaluator="ragas",
        dataset_name="golden_set",
        dataset=dataset,
        predictions=predictions,
        run_id="eval-empty-sample",
    )

    assert detail.status == "success"
    assert detail.summary == {
        "sample_count": 2,
        "prediction_count": 2,
        "metric_count": 2,
        "empty_sample_count": 1,
        "coverage_rate": 0.5,
        "scored_sample_count_by_metric": {
            "faithfulness": 1,
            "answer_relevancy": 1,
        },
        "skipped_sample_count_by_metric": {
            "faithfulness": 1,
            "answer_relevancy": 1,
        },
    }
    sample_results = repository.sample_result_batches[0][1]
    assert [result.sample_id for result in sample_results] == ["sample-ok", "sample-empty"]
    assert sample_results[0].metrics == {"faithfulness": 0.8, "answer_relevancy": 0.75}
    assert sample_results[1].metrics == {}
    assert sample_results[1].retrieved_contexts == ()
    assert sample_results[1].context_chunk_ids == ()
    assert dict(sample_results[1].error or {}) == {
        "empty_result": True,
        "empty_reason": "self_rag_low_confidence",
        "skipped_metrics": ("faithfulness", "answer_relevancy"),
        "scored_metrics": (),
    }
@pytest.mark.unit
def test_evaluation_service_persists_all_empty_predictions_without_calling_evaluator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All-empty runs should report zero coverage instead of invoking Ragas."""

    class FailingEvaluator:
        """Raise if an all-empty run tries to call generation metrics."""

        def evaluate_with_samples(
            self,
            dataset: list[Mapping[str, Any]],
            predictions: list[Mapping[str, Any]],
        ) -> Mapping[str, Any]:
            """The service should not call this for all-empty predictions."""

            raise AssertionError("all-empty runs must not call evaluator")

    repository = _FakeEvaluationRepository()
    monkeypatch.setattr(EvaluatorFactory, "create", lambda **kwargs: FailingEvaluator())
    service = EvaluationService(
        SimpleNamespace(),
        repository=repository,
        clock=lambda: datetime(2026, 6, 27, 12, 0, tzinfo=UTC),
    )
    dataset = [
        {
            "id": "sample-empty",
            "collection": "faq",
            "question": "空调开机有异味怎么办？",
            "golden_answer": "应检查滤网、蒸发器和排水区域。",
        },
    ]
    predictions = [
        {
            "sample_id": "sample-empty",
            "answer": "未检索到足够可信的内部知识。",
            "contexts": [],
            "retrieved_contexts": [],
            "context_chunk_ids": [],
            "query_trace_id": "trace-empty",
            "effective_collection": "faq",
            "error": {
                "empty_result": True,
                "empty_reason": "self_rag_low_confidence",
                "skipped_metrics": ["faithfulness", "answer_relevancy"],
                "scored_metrics": [],
            },
        },
    ]

    detail = service.run_evaluation(
        collection_id="faq",
        evaluator="ragas",
        dataset_name="golden_set",
        dataset=dataset,
        predictions=predictions,
        run_id="eval-all-empty",
    )

    assert detail.status == "success"
    assert detail.metrics == {}
    assert detail.summary == {
        "sample_count": 1,
        "prediction_count": 1,
        "metric_count": 0,
        "empty_sample_count": 1,
        "coverage_rate": 0.0,
        "scored_sample_count_by_metric": {},
        "skipped_sample_count_by_metric": {},
    }
    assert repository.result_batches == [("eval-all-empty", [])]
    assert repository.sample_result_batches[0][1][0].error is not None

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
                "expected_doc_ids": ["shopping_guides/headphones.md#wireless"],
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
                            "expected_doc_ids": ["shopping_guides/a.md#x"],
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
                    "expected_doc_ids": ["shopping_guides/microwave.md"],
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
                run_id=kwargs["run_id"],
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
        ) -> dict[str, Any]:
            """Record the production-shaped request and emulate completed chat."""

            captured.setdefault("aimodel_requests", []).append(
                {"question": question}
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
    monkeypatch.setattr(
        run_evaluation_module,
        "_build_evaluation_runtime",
        lambda *args: FakeRuntime(),
    )
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


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_evaluation_async_builds_predictions_and_records_sample_errors() -> None:
    """Build async predictions without letting one sample failure abort the batch."""

    dataset = [
        {
            "id": "sample-ok",
            "collection": "shopping_guides",
            "question": "如何挑选微波炉？",
            "golden_answer": "关注容量和加热方式。",
            "expected_doc_ids": ["shopping_guides/microwave.md"],
        },
        {
            "id": "sample-failed",
            "collection": "faq",
            "question": "触发失败样本",
            "golden_answer": "应记录失败。",
            "expected_doc_ids": ["faq/failure.md"],
        },
    ]

    class AsyncRuntime:
        """Return one async execution and fail one selected sample."""

        async def execute(self, query: str, **kwargs: Any) -> Any:
            """Emulate the async query runtime used by evaluation."""

            if "失败" in query:
                raise RuntimeError("query runtime failed")
            return SimpleNamespace(
                final_results=[SimpleNamespace(chunk_id="chunk-1", score=0.91)],
                response=SimpleNamespace(
                    content="[1] 微波炉应关注容量。",
                    citations=[],
                    images=[],
                ),
            )

    class FakeChunkLookup:
        """Resolve one chunk for the successful prediction."""

        def get_by_ids(self, chunk_ids: list[str]) -> list[Chunk]:
            """Return deterministic chunk text for Ragas contexts."""

            assert chunk_ids == ["chunk-1"]
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

    predictions = await run_evaluation_module.run_evaluation_async(
        dataset,
        runtime=AsyncRuntime(),
        chunk_lookup=FakeChunkLookup(),
        collection_override=None,
        top_k=1,
        no_rerank=False,
        answer_source=run_evaluation_module.EvaluationAnswerSource.RAG,
        aimodel_client=None,
        message_repository=None,
        query_trace_repository=None,
        max_sample_concurrency=2,
        reporter=None,
    )

    assert [prediction["sample_id"] for prediction in predictions] == [
        "sample-ok",
        "sample-failed",
    ]
    assert predictions[0].get("error") is None
    assert predictions[0]["query_trace_id"].startswith("query-eval-")
    assert predictions[1]["error"] == {
        "type": "RuntimeError",
        "message": "query runtime failed",
    }
    assert predictions[1]["answer_source"] == "rag"
    assert predictions[1]["contexts"] == ["Evaluation sample failed before retrieval completed."]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ragas_evaluator_client_async_evaluate_uses_metric_concurrency() -> None:
    """Expose an async Ragas client path while preserving run_config observability."""

    captured: dict[str, Any] = {}

    def fake_evaluate(
        rows: list[dict[str, Any]],
        *,
        metrics: tuple[str, ...],
        run_config: Mapping[str, int] | None = None,
    ) -> dict[str, float]:
        """Capture the async wrapper's backend call."""

        captured["rows"] = rows
        captured["metrics"] = metrics
        captured["run_config"] = run_config
        return {"faithfulness": 1.0}

    client = RagasEvaluatorClient(
        metric_names=("faithfulness",),
        evaluate_fn=fake_evaluate,
    )

    result = await client.async_evaluate_with_samples(
        [
            {
                "question": "如何挑选微波炉？",
                "golden_answer": "关注容量。",
            }
        ],
        [
            {
                "answer": "关注容量。",
                "contexts": ["微波炉选购关注容量。"],
            }
        ],
        max_metric_concurrency=1,
    )

    assert result["metrics"] == {"faithfulness": 1.0}
    assert captured["metrics"] == ("faithfulness",)
    assert captured["run_config"] == {"timeout_seconds": 300, "max_workers": 1}


class _ObservedFakeLLM:
    """Return one deterministic evaluation answer for Ragas observer tests."""

    def chat(self, messages: list[Any]) -> Any:
        """Record prompt length indirectly while returning provider metadata."""

        assert messages
        return SimpleNamespace(
            content="faithful answer",
            provider="fake",
            model="fake-eval",
        )


class _ObservedFakeEmbedding:
    """Return deterministic vectors for Ragas observer tests."""

    provider = "fake-embedding"
    model = "fake-embedding-v1"

    def embed(self, text: str) -> list[float]:
        """Return one fixed vector for a query string."""

        assert text
        return [1.0, 0.0, 0.0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Return one fixed vector per document string."""

        assert texts
        return [[1.0, 0.0, 0.0] for _ in texts]

class _FakeEvaluationRepository:
    """Capture evaluation writes without opening PostgreSQL in unit tests."""

    def __init__(self) -> None:
        """Initialize in-memory lists that mirror repository write calls."""

        self.run_records: list[EvaluationRunRecord] = []
        self.result_batches: list[tuple[str, list[EvaluationResultRecord]]] = []
        self.sample_result_batches: list[tuple[str, list[EvaluationSampleResultRecord]]] = []

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

    def upsert_sample_results(
        self,
        run_id: str,
        results: list[EvaluationSampleResultRecord],
    ) -> list[EvaluationSampleResultRecord]:
        """Store one sample diagnostic batch and return it in caller order."""

        self.sample_result_batches.append((run_id, results))
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
