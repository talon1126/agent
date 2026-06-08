"""Unit tests for the core TraceContext and TraceController contracts.

Phase F starts the observability layer with in-memory trace construction. These
tests protect the low-intrusion contract that ingestion/query pipelines will
use later: business stages call ``record_stage()`` with summaries, and a
controller flushes one JSON-compatible snapshot to a caller-provided sink.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.core.trace import TraceContext, TraceController


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
