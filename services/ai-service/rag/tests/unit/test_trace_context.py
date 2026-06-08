"""Unit tests for the core TraceContext and TraceController contracts.

Phase F starts the observability layer with in-memory trace construction. These
tests protect the low-intrusion contract that ingestion/query pipelines will
use later: business stages call ``record_stage()`` with summaries, and a
controller flushes one JSON-compatible snapshot to a caller-provided sink.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from src.core.trace import TraceContext, TraceController
from src.observability.structured_log import JsonFormatter
from src.storage.trace_log_storage import JsonlTraceWriter


def test_query_trace_requires_raw_query_and_basic_info() -> None:
    """Query traces must expose the documented request identity fields."""

    started_at = datetime(2026, 6, 8, 10, 0, 0, tzinfo=UTC)
    context = TraceContext.query(
        trace_id="trace-query-source",
        collection="shopping_guides",
        raw_query="如何挑选高性价比无线耳机？",
        request_source="aimodel",
        started_at=started_at,
    )

    assert context.to_dict()["basic_info"] == {
        "trace_id": "trace-query-source",
        "trace_type": "query",
        "started_at": "2026-06-08T10:00:00+00:00",
        "collection": "shopping_guides",
        "raw_query": "如何挑选高性价比无线耳机？",
        "request_source": "aimodel",
    }

    with pytest.raises(ValueError, match="raw_query"):
        TraceContext.query(
            collection="shopping_guides",
            raw_query=" ",
            request_source="mcp",
        )
    with pytest.raises(ValueError, match="raw_query"):
        TraceContext(trace_type="query", collection="shopping_guides")


def test_json_formatter_serializes_log_records_as_single_json_object() -> None:
    """Structured trace logs must be valid JSON Lines without ad-hoc strings."""

    record = JsonFormatter.make_record(
        logger_name="aimodel_rag.trace",
        level_name="INFO",
        message="trace flushed",
        extra={
            "trace_id": "trace-query-json",
            "trace_type": "query",
            "payload": {"status": "success", "duration_ms": 12.5},
        },
    )

    parsed = json.loads(JsonFormatter().format(record))

    assert parsed["logger"] == "aimodel_rag.trace"
    assert parsed["level"] == "INFO"
    assert parsed["message"] == "trace flushed"
    assert parsed["trace_id"] == "trace-query-json"
    assert parsed["trace_type"] == "query"
    assert parsed["payload"] == {"status": "success", "duration_ms": 12.5}
    assert isinstance(parsed["timestamp"], str)


def test_jsonl_trace_writer_appends_valid_trace_snapshots(tmp_path) -> None:
    """Trace writer should create parent directories and append JSON snapshots."""

    log_path = tmp_path / "src" / "logs" / "traces.jsonl"
    writer = JsonlTraceWriter(log_path)
    first_trace = TraceContext.query(
        trace_id="trace-query-jsonl-1",
        collection="shopping_guides",
        raw_query="推荐解压玩具",
        request_source="aimodel",
    ).finish_query(
        status="success",
        top_k_results=[{"chunk_id": "chunk-a", "rank": 1}],
        candidate_count_by_stage={"dense": 1, "sparse": 0, "fusion": 1},
        fallback_used=False,
        empty_result=False,
    )
    second_trace = TraceContext.ingestion(
        trace_id="trace-ingestion-jsonl-2",
        collection="shopping_guides",
        source_uri="shopping_guides/relax-toys.pdf",
        source_hash="e" * 64,
    ).finish_ingestion(
        status="skipped",
        document_status="skipped",
        chunk_count=0,
        embedded_count=0,
        skipped_count=1,
        index_ready=True,
    )

    writer.write(first_trace)
    writer(second_trace)

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    parsed = [json.loads(line) for line in lines]
    assert [item["trace_id"] for item in parsed] == [
        "trace-query-jsonl-1",
        "trace-ingestion-jsonl-2",
    ]
    assert parsed[0]["basic_info"]["raw_query"] == "推荐解压玩具"
    assert parsed[1]["basic_info"]["source_hash"] == "e" * 64


def test_trace_controller_flush_can_use_jsonl_trace_writer(tmp_path) -> None:
    """TraceController sink integration should append the flushed snapshot."""

    log_path = tmp_path / "traces.jsonl"
    writer = JsonlTraceWriter(log_path)
    context = TraceContext.query(
        trace_id="trace-query-controller-jsonl",
        collection="shopping_guides",
        raw_query="如何选择无线耳机",
        request_source="mcp",
    )
    controller = TraceController(context, sink=writer)

    flushed = controller.flush(
        status="success",
        summary_metrics={
            "top_k_results": [],
            "candidate_count_by_stage": {"dense": 0},
            "fallback_used": False,
        },
        evaluation_metrics={"empty_result": True},
    )

    parsed = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert parsed["trace_id"] == "trace-query-controller-jsonl"
    assert parsed["summary_metrics"] == flushed["summary_metrics"]
    assert parsed["evaluation_metrics"] == {"empty_result": True}


def test_query_trace_records_only_documented_stages() -> None:
    """Query stage recording should stay aligned with the DEV_SPEC stages."""

    context = TraceContext.query(
        trace_id="trace-query-stages",
        collection="shopping_guides",
        raw_query="推荐适合办公室的解压玩具",
        request_source="mcp",
    )

    context.record_query_stage(
        "dense",
        duration_ms=18,
        input_summary={"embedding_model": "text-embedding-v4"},
        output_summary={
            "top_k": 3,
            "candidates": [
                {"chunk_id": "chunk-a", "score": 0.91},
                {"chunk_id": "chunk-b", "score": 0.88},
            ],
        },
        method="pgvector_search",
        provider="pgvector",
        candidate_count=2,
        details={"vector_dimension": 1536},
    )

    stage = context.to_dict()["stages"][0]
    assert stage["stage"] == "dense"
    assert stage["duration_ms"] == 18.0
    assert stage["candidate_count"] == 2
    assert stage["method"] == "pgvector_search"
    assert stage["provider"] == "pgvector"
    assert stage["details"] == {"vector_dimension": 1536}
    assert stage["output_summary"]["candidates"] == [
        {"chunk_id": "chunk-a", "score": 0.91},
        {"chunk_id": "chunk-b", "score": 0.88},
    ]

    with pytest.raises(ValueError, match="query stage"):
        context.record_query_stage("load")


def test_query_trace_finish_adds_summary_and_evaluation_sections() -> None:
    """Finish should normalize documented query summary/evaluation keys."""

    started_at = datetime(2026, 6, 8, 10, 0, 0, tzinfo=UTC)
    finished_at = started_at + timedelta(milliseconds=320)
    context = TraceContext.query(
        trace_id="trace-query-summary",
        collection="shopping_guides",
        raw_query="对比棉花娃娃和捏捏乐哪个更解压",
        request_source="aimodel",
        started_at=started_at,
    )

    snapshot = context.finish_query(
        status="success",
        finished_at=finished_at,
        top_k_results=[
            {"chunk_id": "chunk-a", "rank": 1, "score": 0.96},
            {"chunk_id": "chunk-b", "rank": 2, "score": 0.91},
        ],
        candidate_count_by_stage={
            "dense": 20,
            "sparse": 14,
            "fusion": 26,
            "filter": 18,
            "rerank": 5,
        },
        fallback_used=True,
        error=None,
        query_document_relevance=0.89,
        citation_hit_rate=1.0,
        rerank_delta={"chunk-a": {"before": 3, "after": 1}},
        empty_result=False,
    )

    assert snapshot["summary_metrics"] == {
        "top_k_results": [
            {"chunk_id": "chunk-a", "rank": 1, "score": 0.96},
            {"chunk_id": "chunk-b", "rank": 2, "score": 0.91},
        ],
        "candidate_count_by_stage": {
            "dense": 20,
            "sparse": 14,
            "fusion": 26,
            "filter": 18,
            "rerank": 5,
        },
        "fallback_used": True,
        "error": None,
        "total_duration_ms": 320.0,
    }
    assert snapshot["evaluation_metrics"] == {
        "query_document_relevance": 0.89,
        "citation_hit_rate": 1.0,
        "rerank_delta": {"chunk-a": {"before": 3, "after": 1}},
        "empty_result": False,
    }


def test_query_trace_rejects_invalid_summary_metrics() -> None:
    """Reject invalid query counts, boolean flags, and quality ratios."""

    context = TraceContext.query(
        collection="shopping_guides",
        raw_query="高性价比无线耳机怎么选",
        request_source="dashboard",
    )

    with pytest.raises(ValueError, match="candidate_count_by_stage"):
        context.finish_query(
            status="success",
            top_k_results=[],
            candidate_count_by_stage={"dense": -1},
            fallback_used=False,
        )
    with pytest.raises(ValueError, match="fallback_used"):
        context.finish_query(
            status="success",
            top_k_results=[],
            candidate_count_by_stage={"dense": 0},
            fallback_used="false",
        )
    with pytest.raises(ValueError, match="query_document_relevance"):
        context.finish_query(
            status="success",
            top_k_results=[],
            candidate_count_by_stage={"dense": 0},
            fallback_used=False,
            query_document_relevance=1.2,
        )
    with pytest.raises(ValueError, match="empty_result"):
        context.finish_query(
            status="success",
            top_k_results=[],
            candidate_count_by_stage={"dense": 0},
            fallback_used=False,
            empty_result="no",
        )


def test_ingestion_trace_requires_source_identity_and_basic_info() -> None:
    """Ingestion traces must expose the documented source identity fields."""

    started_at = datetime(2026, 6, 8, 10, 0, 0, tzinfo=UTC)
    context = TraceContext.ingestion(
        trace_id="trace-ingestion-source",
        collection="shopping_guides",
        source_uri="shopping_guides/relax-toys.pdf",
        source_hash="a" * 64,
        started_at=started_at,
    )

    assert context.to_dict()["basic_info"] == {
        "trace_id": "trace-ingestion-source",
        "trace_type": "ingestion",
        "started_at": "2026-06-08T10:00:00+00:00",
        "collection": "shopping_guides",
        "source_uri": "shopping_guides/relax-toys.pdf",
        "source_hash": "a" * 64,
    }

    with pytest.raises(ValueError, match="source_hash"):
        TraceContext.ingestion(
            collection="shopping_guides",
            source_uri="shopping_guides/relax-toys.pdf",
            source_hash="not-a-sha256",
        )


def test_ingestion_trace_records_only_documented_stages() -> None:
    """Ingestion stage recording should stay aligned with the DEV_SPEC stages."""

    context = TraceContext.ingestion(
        trace_id="trace-ingestion-stages",
        collection="shopping_guides",
        source_uri="shopping_guides/relax-toys.pdf",
        source_hash="b" * 64,
    )

    context.record_ingestion_stage(
        "dedup",
        duration_ms=3.5,
        input_summary={"source_hash": "b" * 64},
        output_summary={"skip_ingestion": False},
        method="sha256",
        provider="DocumentRepository",
        details={"successful_hash_hit": False, "skip_reason": None},
    )

    stage = context.to_dict()["stages"][0]
    assert stage["stage"] == "dedup"
    assert stage["duration_ms"] == 3.5
    assert stage["method"] == "sha256"
    assert stage["provider"] == "DocumentRepository"
    assert stage["details"] == {
        "successful_hash_hit": False,
        "skip_reason": None,
    }

    with pytest.raises(ValueError, match="ingestion stage"):
        context.record_ingestion_stage("query_processing")


def test_ingestion_trace_finish_adds_summary_and_evaluation_sections() -> None:
    """Finish should normalize the documented ingestion summary/evaluation keys."""

    started_at = datetime(2026, 6, 8, 10, 0, 0, tzinfo=UTC)
    finished_at = started_at + timedelta(milliseconds=500)
    context = TraceContext.ingestion(
        trace_id="trace-ingestion-summary",
        collection="shopping_guides",
        source_uri="shopping_guides/relax-toys.pdf",
        source_hash="c" * 64,
        started_at=started_at,
    )

    snapshot = context.finish_ingestion(
        status="success",
        finished_at=finished_at,
        document_status="success",
        chunk_count=12,
        embedded_count=10,
        skipped_count=2,
        chunk_quality_score=0.92,
        noise_reduction_summary={"removed_headers": 3},
        embedding_coverage=0.83,
        index_ready=True,
    )

    assert snapshot["summary_metrics"] == {
        "document_status": "success",
        "chunk_count": 12,
        "embedded_count": 10,
        "skipped_count": 2,
        "error": None,
        "total_duration_ms": 500.0,
    }
    assert snapshot["evaluation_metrics"] == {
        "chunk_quality_score": 0.92,
        "noise_reduction_summary": {"removed_headers": 3},
        "embedding_coverage": 0.83,
        "index_ready": True,
    }


def test_ingestion_trace_rejects_invalid_summary_metrics() -> None:
    """Reject invalid identity, final status, counts, and readiness values."""

    with pytest.raises(ValueError, match="source_uri"):
        TraceContext(trace_type="ingestion", collection="shopping_guides")

    context = TraceContext.ingestion(
        collection="shopping_guides",
        source_uri="shopping_guides/relax-toys.pdf",
        source_hash="d" * 64,
    )

    with pytest.raises(ValueError, match="chunk_count"):
        context.finish_ingestion(
            status="success",
            document_status="success",
            chunk_count=-1,
            embedded_count=0,
            skipped_count=0,
        )
    with pytest.raises(ValueError, match="document_status"):
        context.finish_ingestion(
            status="success",
            document_status="processing",
            chunk_count=0,
            embedded_count=0,
            skipped_count=0,
        )
    with pytest.raises(ValueError, match="index_ready"):
        context.finish_ingestion(
            status="success",
            document_status="success",
            chunk_count=0,
            embedded_count=0,
            skipped_count=0,
            index_ready="yes",
        )


def test_trace_context_records_stage_duration_and_summaries() -> None:
    """Record one Query stage with timing, provider, method, and summaries."""

    started_at = datetime(2026, 6, 8, 10, 0, 0, tzinfo=UTC)
    context = TraceContext(
        trace_id="trace-query-001",
        trace_type="query",
        collection="shopping_guides",
        started_at=started_at,
        raw_query="如何挑选高性价比无线耳机？",
        request_source="mcp",
    )

    context.record_stage(
        "query_processing",
        duration_ms=12.5,
        input_summary={"raw_query": "如何挑选高性价比无线耳机？"},
        output_summary={"rewritten_query": "高性价比无线耳机选购要点"},
        method="rewrite",
        provider="deepseek",
        details={"intent": "buying_guide"},
    )

    snapshot = context.to_dict()

    assert snapshot["basic_info"] == {
        "trace_id": "trace-query-001",
        "trace_type": "query",
        "started_at": "2026-06-08T10:00:00+00:00",
        "collection": "shopping_guides",
        "raw_query": "如何挑选高性价比无线耳机？",
        "request_source": "mcp",
    }
    assert snapshot["stages"] == [
        {
            "stage": "query_processing",
            "duration_ms": 12.5,
            "status": "success",
            "input_summary": {"raw_query": "如何挑选高性价比无线耳机？"},
            "output_summary": {"rewritten_query": "高性价比无线耳机选购要点"},
            "method": "rewrite",
            "provider": "deepseek",
            "details": {"intent": "buying_guide"},
            "error": None,
        }
    ]


def test_trace_controller_flush_finishes_context_and_writes_snapshot() -> None:
    """Flush should finish a trace, merge metrics, and call the sink once."""

    started_at = datetime(2026, 6, 8, 10, 0, 0, tzinfo=UTC)
    finished_at = started_at + timedelta(milliseconds=250)
    payloads: list[dict] = []
    context = TraceContext(
        trace_id="trace-ingestion-001",
        trace_type="ingestion",
        collection="shopping_guides",
        started_at=started_at,
        source_uri="shopping_guides/relax-toys.pdf",
        source_hash="a" * 64,
    )
    controller = TraceController(
        context,
        sink=payloads.append,
        clock=lambda: finished_at,
    )

    controller.record_stage(
        "load",
        duration_ms=40,
        input_summary={"source_uri": "shopping_guides/relax-toys.pdf"},
        output_summary={"document_id": "doc-relax-toys"},
        method="pdf_loader",
        provider="markitdown",
    )
    flushed = controller.flush(
        status="success",
        summary_metrics={"chunk_count": 8, "document_status": "success"},
        evaluation_metrics={"index_ready": True},
    )

    assert payloads == [flushed]
    assert flushed["basic_info"]["trace_type"] == "ingestion"
    assert flushed["summary_metrics"]["chunk_count"] == 8
    assert flushed["summary_metrics"]["document_status"] == "success"
    assert flushed["summary_metrics"]["total_duration_ms"] == 250.0
    assert flushed["evaluation_metrics"] == {"index_ready": True}
    assert flushed["finished_at"] == "2026-06-08T10:00:00.250000+00:00"
    assert flushed["status"] == "success"


def test_trace_context_copies_stage_payloads_and_records_error_details() -> None:
    """Stage details should remain stable and JSON-safe after inputs mutate."""

    observed_at = datetime(2026, 6, 8, 10, 0, 1, tzinfo=UTC)
    details = {"fallback_used": True, "fallback_reason": "reranker_timeout"}
    output_summary = {"candidate_count": 5, "chunk_ids": ("chunk-a", "chunk-b")}
    context = TraceContext(
        trace_id="trace-query-002",
        trace_type="query",
        collection="shopping_guides",
        raw_query="推荐解压玩具",
        request_source="aimodel",
    )

    context.record_stage(
        "rerank",
        duration_ms=5,
        output_summary=output_summary,
        status="failed",
        details={**details, "observed_at": observed_at},
        error={"code": "reranker_timeout", "message": "reranker timed out"},
    )
    details["fallback_reason"] = "mutated"
    output_summary["candidate_count"] = 0

    stage = context.to_dict()["stages"][0]
    assert stage["details"] == {
        "fallback_used": True,
        "fallback_reason": "reranker_timeout",
        "observed_at": "2026-06-08T10:00:01+00:00",
    }
    assert stage["output_summary"] == {
        "candidate_count": 5,
        "chunk_ids": ["chunk-a", "chunk-b"],
    }
    assert stage["error"] == {
        "code": "reranker_timeout",
        "message": "reranker timed out",
    }


def test_trace_context_rejects_invalid_trace_contracts() -> None:
    """Reject invalid trace types, blank stages, and negative durations."""

    with pytest.raises(ValueError, match="trace_type"):
        TraceContext(
            trace_id="trace-invalid",
            trace_type="dashboard",
            collection="shopping_guides",
        )

    context = TraceContext(
        trace_id="trace-query-003",
        trace_type="query",
        collection="shopping_guides",
        raw_query="怎么选无线耳机",
    )

    with pytest.raises(ValueError, match="stage"):
        context.record_stage(" ")
    with pytest.raises(ValueError, match="duration_ms"):
        context.record_stage("dense", duration_ms=-1)
    with pytest.raises(ValueError, match="status"):
        context.record_stage("dense", status="unknown")
    with pytest.raises(ValueError, match="status"):
        context.finish(status="unknown")
