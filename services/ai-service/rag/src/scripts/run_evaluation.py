"""Run golden-set evaluation against the production RAG/AImodel path.

This script is the executable Phase G bridge between the static golden set,
the configured retrieval stack, Ragas-compatible generation metrics, and
PostgreSQL evaluation history. The default answer source is the final AImodel
assistant message because that is what users actually read. The explicit
``rag`` mode still reuses ``QueryRuntime`` for lower-level context debugging.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx

from src.core.config import (
    RAG_ROOT,
    EvaluationAImodelSettings,
    RagSettings,
    enabled_generation_metrics,
    load_settings,
)
from src.core.types import Chunk
from src.libs.vector_store import VectorStoreFactory
from src.observability.services import EvaluationService
from src.scripts.query import (
    _build_runtime,
    _create_pool,
    _load_local_environment,
)
from src.storage.postgres import PostgresPool, init_schema
from src.storage.repositories import TraceRepository

EVALUATION_LOG_PATH = RAG_ROOT / "src" / "logs" / "evaluation.log.jsonl"
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


class EvaluationReporter:
    """Emit human-readable and machine-readable progress for long evaluations.

    The reporter is intentionally independent from the evaluation business
    logic. It writes compact console status to stderr through the injected
    writer while appending structured JSON Lines events for later debugging.
    Final CLI success output still goes through ``run_evaluation_cli(output=...)``
    so shell callers can parse that JSON without filtering progress messages.
    """

    def __init__(
        self,
        *,
        console: Callable[[str], Any],
        log_path: Path = EVALUATION_LOG_PATH,
        clock: Callable[[], float] = time.monotonic,
        refresh: bool = False,
        heartbeat_interval_seconds: float = 0.0,
    ) -> None:
        """Create a reporter for one evaluation process.

        Args:
            console: Writer for readable progress lines, normally stderr.
            log_path: JSON Lines diagnostics file path. Parent directories are
                created lazily when the first event is written.
            clock: Monotonic clock used for deterministic duration tests.
            refresh: Whether console status should use carriage-return single
                line refreshes. Tests and non-terminal writers can keep this
                disabled to receive append-only messages.
            heartbeat_interval_seconds: Optional refresh interval used by the
                CLI to keep elapsed time moving while a provider call blocks.
                A value of zero disables the background heartbeat for tests.
        """

        self._console = console
        self._log_path = log_path
        self._clock = clock
        self._refresh = refresh
        self._heartbeat_interval_seconds = max(heartbeat_interval_seconds, 0.0)
        self._run_id: str | None = None
        self._dataset_name: str | None = None
        self._sample_count: int | None = None
        self._run_started_at: float | None = None
        self._active_step_started_at: float | None = None
        self._step_starts: dict[str, float] = {}
        self._console_lock = threading.Lock()
        self._heartbeat_stop: threading.Event | None = None
        self._heartbeat_thread: threading.Thread | None = None
        self._heartbeat_message = ""

    def run_started(
        self,
        *,
        run_id: str,
        dataset_name: str,
        sample_count: int,
        answer_source: str,
        top_k: int,
        no_rerank: bool,
        evaluator: str,
        metric_names: Sequence[str],
        collections: Sequence[str],
    ) -> None:
        """Record the beginning of an evaluation run.

        Args:
            run_id: Stable run identifier used by console and JSONL events.
            dataset_name: Golden set name without file extension.
            sample_count: Number of samples that will be evaluated.
            answer_source: ``message`` or ``rag`` answer source mode.
            top_k: Retrieval result count requested by the run.
            no_rerank: Whether rerank is disabled for the run.
            evaluator: Evaluator provider name, usually ``ragas``.
            metric_names: Configured generation metrics passed to the evaluator.
            collections: Collections represented by the selected samples.
        """

        now = self._clock()
        self._run_id = run_id
        self._dataset_name = dataset_name
        self._sample_count = sample_count
        self._run_started_at = now
        self._write_console(
            f"[{run_id}] started dataset={dataset_name} samples={sample_count} "
            f"answer_source={answer_source} top_k={top_k} "
            f"rerank={'off' if no_rerank else 'on'} evaluator={evaluator} "
            f"metrics={','.join(metric_names)} collections={','.join(collections)}"
        )
        self._write_event(
            {
                "event": "run_started",
                "run_id": run_id,
                "dataset_name": dataset_name,
                "sample_count": sample_count,
                "answer_source": answer_source,
                "top_k": top_k,
                "no_rerank": no_rerank,
                "evaluator": evaluator,
                "metric_names": list(metric_names),
                "collections": list(collections),
                "status": "started",
                "timestamp_monotonic": now,
            }
        )

    def step_started(self, step: str, *, details: Mapping[str, Any] | None = None) -> None:
        """Record that a run-level stage has started.

        Args:
            step: Stable stage name such as ``build_predictions`` or
                ``ragas_evaluation``.
            details: Optional small diagnostic payload for the stage.
        """

        now = self._clock()
        self._step_starts[step] = now
        self._active_step_started_at = now
        self._write_console(f"[{self._run_label()}] {step} started")
        self._start_heartbeat(f"[{self._run_label()}] {step} running")
        self._write_event(
            self._base_event(
                {
                    "event": "step_started",
                    "step": step,
                    "status": "started",
                    "timestamp_monotonic": now,
                    **dict(details or {}),
                }
            )
        )

    def step_done(self, step: str, *, details: Mapping[str, Any] | None = None) -> None:
        """Record that a run-level stage completed successfully.

        Args:
            step: Stage name previously passed to ``step_started``.
            details: Optional small diagnostic payload for the completed stage.
        """

        now = self._clock()
        duration_ms = self._duration_ms(self._step_starts.pop(step, now), now)
        self._stop_heartbeat()
        self._active_step_started_at = None
        self._write_console(
            f"[{self._run_label()}] {step} completed",
            final=True,
        )
        self._write_event(
            self._base_event(
                {
                    "event": "step_done",
                    "step": step,
                    "status": "success",
                    "duration_ms": duration_ms,
                    "timestamp_monotonic": now,
                    **dict(details or {}),
                }
            )
        )

    def sample_started(
        self,
        *,
        sample_index: int,
        sample_count: int,
        sample: Mapping[str, Any],
    ) -> None:
        """Record that prediction generation started for one golden sample.

        Args:
            sample_index: One-based sample index in the current run.
            sample_count: Total sample count in the current run.
            sample: Golden set sample containing ``id``, ``collection`` and
                ``question`` fields.
        """

        sample_id = _sample_id(sample)
        collection = str(sample.get("collection", ""))
        question_preview = _preview_text(str(sample.get("question", "")))
        self._write_console(
            f"[{self._run_label()}] [{sample_index}/{sample_count}] "
            f"{sample_id} collection={collection} "
            f"question_chars={len(str(sample.get('question', '')))}"
        )
        self._write_event(
            self._base_event(
                {
                    "event": "sample_started",
                    "sample_index": sample_index,
                    "sample_count": sample_count,
                    "sample_id": sample_id,
                    "collection": collection,
                    "question_preview": question_preview,
                    "status": "started",
                    "timestamp_monotonic": self._clock(),
                }
            )
        )

    def sample_step_done(
        self,
        *,
        sample_index: int,
        sample: Mapping[str, Any],
        step: str,
        status: str = "success",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        """Record a completed sub-step inside one sample prediction.

        Args:
            sample_index: One-based sample index in the current run.
            sample: Golden set sample associated with the event.
            step: Stable sub-step name such as ``aimodel_chat``.
            status: Sub-step outcome, normally ``success``.
            details: Optional small payload such as message or trace IDs.
        """

        payload = dict(details or {})
        self._write_console(
            _sample_step_status_text(
                sample_index=sample_index,
                sample_count=self._sample_count,
                sample=sample,
                step=step,
                status=status,
                details=payload,
            )
        )
        self._write_event(
            self._base_event(
                {
                    "event": "sample_step_done",
                    "sample_index": sample_index,
                    "sample_count": self._sample_count,
                    "sample_id": _sample_id(sample),
                    "collection": str(sample.get("collection", "")),
                    "step": step,
                    "status": status,
                    "timestamp_monotonic": self._clock(),
                    **payload,
                }
            )
        )

    def failed(
        self,
        *,
        step: str,
        error: Exception,
        sample: Mapping[str, Any] | None = None,
        sample_index: int | None = None,
    ) -> None:
        """Record a failure with enough context to locate the broken stage.

        Args:
            step: Run-level or sample-level step that raised the exception.
            error: Exception raised by the evaluation flow.
            sample: Optional sample active when the failure occurred.
            sample_index: Optional one-based index for the active sample.
        """

        now = self._clock()
        duration_ms = (
            self._duration_ms(self._run_started_at, now)
            if self._run_started_at is not None
            else None
        )
        sample_payload = (
            {
                "sample_index": sample_index,
                "sample_count": self._sample_count,
                "sample_id": _sample_id(sample),
                "collection": str(sample.get("collection", "")),
            }
            if sample is not None
            else {}
        )
        message = str(error)
        self._stop_heartbeat()
        self._write_console(
            f"[{self._run_label()}] {step} failed: "
            f"{error.__class__.__name__}: {message}",
            final=True,
        )
        self._write_event(
            self._base_event(
                {
                    "event": "failed",
                    "step": step,
                    "status": "failed",
                    "duration_ms": duration_ms,
                    "error_type": error.__class__.__name__,
                    "error_message": message,
                    "timestamp_monotonic": now,
                    **sample_payload,
                }
            )
        )

    def completed(self, *, detail: Any) -> None:
        """Record the final persisted evaluation result summary.

        Args:
            detail: EvaluationRunDetail-like object returned by
                ``EvaluationService.run_evaluation``.
        """

        now = self._clock()
        duration_ms = (
            self._duration_ms(self._run_started_at, now)
            if self._run_started_at is not None
            else None
        )
        metrics = dict(getattr(detail, "metrics", {}) or {})
        summary = dict(getattr(detail, "summary", {}) or {})
        run_id = str(getattr(detail, "run_id", self._run_label()))
        self._stop_heartbeat()
        self._write_console(
            f"[{run_id}] completed status={getattr(detail, 'status', 'unknown')} "
            f"samples={summary.get('sample_count')} metrics={metrics}",
            final=True,
        )
        self._write_event(
            self._base_event(
                {
                    "event": "completed",
                    "run_id": run_id,
                    "status": getattr(detail, "status", "unknown"),
                    "duration_ms": duration_ms,
                    "metrics": metrics,
                    "summary": summary,
                    "timestamp_monotonic": now,
                }
            )
        )

    def format_elapsed(self, elapsed_seconds: float) -> str:
        """Format elapsed seconds using compact installer-style units.

        Args:
            elapsed_seconds: Seconds since the evaluation run started.

        Returns:
            ``7s`` below one minute, ``1m12s`` below one hour, and
            ``1h01m01s`` for longer runs.
        """

        total_seconds = max(int(elapsed_seconds), 0)
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours}h{minutes:02d}m{seconds:02d}s"
        if minutes:
            return f"{minutes}m{seconds:02d}s"
        return f"{seconds}s"

    def render_status(self, message: str, *, final: bool = False) -> None:
        """Render one elapsed-time status line to the configured console.

        Args:
            message: Human-readable current status without elapsed prefix.
            final: Whether the line should be terminated when refresh mode is
                active, making the last status durable in the terminal.
        """

        self._write_console(message, final=final)

    def ragas_observer(self, event: Mapping[str, Any]) -> None:
        """Receive model-call events emitted by the Ragas adapter.

        Args:
            event: Trace-safe payload from the Ragas adapter. The payload must
                not include full prompts, responses, context text, vectors, API
                keys, or other large/sensitive values.
        """

        self._write_event(self._base_event(dict(event)))
        status = str(event.get("status"))
        status_text = _ragas_status_text(event)
        if status == "started":
            self._start_heartbeat(status_text)
            self.render_status(status_text)
            return
        if status == "failed":
            self._stop_heartbeat()
            self.render_status(status_text, final=True)
            return
        self.render_status(status_text)
        self._start_heartbeat(f"[{self._run_label()}] ragas_evaluation running")

    def _base_event(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Attach run identity fields shared by every structured event."""

        return {
            "run_id": self._run_id,
            "dataset_name": self._dataset_name,
            "timestamp": _shanghai_timestamp(),
            **dict(payload),
        }

    def _write_event(self, payload: Mapping[str, Any]) -> None:
        """Append one JSON event to the diagnostics log file.

        Side Effects:
            Creates the log directory if needed and appends one UTF-8 JSON line.
        """

        self._log_path.parent.mkdir(parents=True, exist_ok=True)
        event = dict(payload)
        event.setdefault("timestamp", _shanghai_timestamp())
        with self._log_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _write_console(self, message: str, *, final: bool = False) -> None:
        """Write one console status with elapsed-time prefix."""

        elapsed = self._console_elapsed_seconds()
        rendered = f"[{self.format_elapsed(elapsed)}] {message}"
        with self._console_lock:
            if self._refresh:
                line_break = "\n" if final else ""
                self._console(f"\r{rendered}{line_break}")
            else:
                self._console(rendered)


    def _console_elapsed_seconds(self) -> float:
        """Return elapsed seconds for the active console scope."""

        if self._active_step_started_at is not None:
            return max(self._clock() - self._active_step_started_at, 0.0)
        if self._run_started_at is not None:
            return max(self._clock() - self._run_started_at, 0.0)
        return 0.0

    def _start_heartbeat(self, message: str) -> None:
        """Start or update the background elapsed-time refresh loop."""

        self._heartbeat_message = message
        if (
            not self._refresh
            or self._heartbeat_interval_seconds <= 0
            or (
                self._heartbeat_thread is not None
                and self._heartbeat_thread.is_alive()
            )
        ):
            return
        self._heartbeat_stop = threading.Event()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop,
            name="rag-evaluation-progress",
            daemon=True,
        )
        self._heartbeat_thread.start()

    def _stop_heartbeat(self) -> None:
        """Stop the background elapsed-time refresh loop if it is active."""

        stop_event = self._heartbeat_stop
        thread = self._heartbeat_thread
        self._heartbeat_stop = None
        self._heartbeat_thread = None
        if stop_event is not None:
            stop_event.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=0.2)

    def _heartbeat_loop(self) -> None:
        """Refresh the current status line until the active step finishes."""

        stop_event = self._heartbeat_stop
        if stop_event is None:
            return
        while not stop_event.wait(self._heartbeat_interval_seconds):
            if not self._heartbeat_message:
                continue
            self._write_console(self._heartbeat_message)

    def _run_label(self) -> str:
        """Return the current run ID or a readable placeholder before startup."""

        return self._run_id or "evaluation"

    @staticmethod
    def _duration_ms(start: float | None, end: float) -> float | None:
        """Return elapsed milliseconds when a start timestamp is available."""

        if start is None:
            return None
        return max((end - start) * 1000, 0.0)


def _shanghai_timestamp() -> str:
    """Return an ISO-8601 Asia/Shanghai timestamp for JSONL diagnostics."""

    return datetime.now(SHANGHAI_TZ).isoformat(timespec="milliseconds")

class EvaluationAnswerSource(StrEnum):
    """Enumerate supported answer sources for generation-quality evaluation.

    ``message`` is the production default because Ragas ``answer`` should be
    the final user-visible assistant response. ``rag`` is retained as an
    explicit debugging mode for inspecting the context package returned by the
    RAG response builder before AImodel turns it into a final answer.
    """

    MESSAGE = "message"
    RAG = "rag"


@dataclass(frozen=True)
class MessageAnswer:
    """Represent one stored AImodel assistant message used as a Ragas answer.

    Attributes:
        message_id: Primary key from the AImodel ``message`` table.
        conversation_id: Conversation that owns the assistant message.
        content: User-visible assistant answer persisted by AImodel.
        query_trace_ids: RAG query traces that AImodel linked to this message.
    """

    message_id: int
    conversation_id: int
    content: str
    query_trace_ids: tuple[str, ...] = ()


class ChunkLookup(Protocol):
    """Describe the chunk text lookup required to build Ragas contexts."""

    def get_by_ids(self, chunk_ids: Sequence[str]) -> list[Chunk]:
        """Return existing chunks in the caller-requested relative order."""


class AImodelEvaluationClient:
    """Call the AImodel chat endpoint for evaluation-only final answers.

    The client consumes the existing SSE chat endpoint but does not interpret
    tool output, mutate RAG state, or write evaluation records. Its only job is
    to trigger the same AImodel path that a user-facing chat request uses so the
    final assistant message is persisted and linked to the RAG query trace by
    AImodel memory code.
    """

    def __init__(self, settings: EvaluationAImodelSettings) -> None:
        """Create a client from typed evaluation settings.

        Args:
            settings: Configured AImodel chat URL, evaluation user id, and HTTP
                timeout from ``settings.evaluation.aimodel``.
        """

        self._settings = settings

    def chat(
        self,
        question: str,
    ) -> dict[str, Any]:
        """Send one golden question to AImodel and return the final done payload.

        Args:
            question: User question from the golden set. Evaluation does not
                force collection selection or RAG usage, so this request follows
                the same routing path as a normal user conversation.

        Returns:
            Parsed SSE ``done`` payload emitted by ``/AImodel/chat``.

        Raises:
            ValueError: If AImodel returns an error event, no ``done`` event, or
                a malformed response body. ``httpx`` transport errors propagate
                to the caller and make the evaluation fail fast.

        Side Effects:
            AImodel persists user and assistant messages in its own tables and
            writes ``message_query_trace`` associations when the Agent calls the
            RAG tool.
        """

        response = httpx.post(
            self._settings.chat_url,
            json={
                "user_id": self._settings.user_id,
                "message": _required_text(question, field_name="question"),
                "links": [],
            },
            timeout=self._settings.timeout_seconds,
        )
        response.raise_for_status()
        return _done_payload_from_sse(response.text)


class MessageAnswerRepository:
    """Read AImodel assistant messages linked to RAG query traces.

    The table is owned by the AImodel side, but the RAG evaluation script reads
    it through the same PostgreSQL database to turn a completed chat response
    into the final assistant answer that Ragas should evaluate.
    """

    def __init__(self, pool: PostgresPool) -> None:
        """Store the open RAG PostgreSQL pool used for read-only lookups."""

        self._pool = pool

    def get_answer_from_chat_result(
        self,
        chat_result: Mapping[str, Any],
    ) -> MessageAnswer | None:
        """Return the assistant message created by one AImodel chat call.

        Args:
            chat_result: Parsed ``event: done`` payload from ``/AImodel/chat``.
                It must include the conversation ID and final answer text.

        Returns:
            ``MessageAnswer`` with all linked query trace IDs, or ``None`` when
            the expected assistant message was not persisted.

        Raises:
            ValueError: If the chat payload misses required identifiers.
        """

        conversation_id = _required_positive_int(
            chat_result.get("conversation_id"),
            field_name="aimodel.conversation_id",
        )
        answer = _required_text(chat_result.get("answer"), field_name="aimodel.answer")
        with self._pool.connection() as connection:
            row = connection.execute(
                """
                SELECT id, conversation_id, content
                FROM message
                WHERE conversation_id = %s
                  AND role = 'assistant'
                  AND content = %s
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (conversation_id, answer),
            ).fetchone()
        if row is None:
            return None
        message_id = int(row[0])
        return MessageAnswer(
            message_id=message_id,
            conversation_id=int(row[1]),
            content=_required_text(row[2], field_name="message.content"),
            query_trace_ids=self._query_trace_ids(message_id),
        )

    def _query_trace_ids(self, message_id: int) -> tuple[str, ...]:
        """Return RAG trace IDs linked to one assistant message in write order."""

        with self._pool.connection() as connection:
            rows = connection.execute(
                """
                SELECT query_trace_id
                FROM message_query_trace
                WHERE message_id = %s
                ORDER BY id ASC
                """,
                (message_id,),
            ).fetchall()
        return tuple(str(row[0]) for row in rows)


class QueryTraceResultRepository:
    """Read ``query_result`` payloads for traces linked to AImodel messages."""

    def __init__(self, pool: PostgresPool) -> None:
        """Create a trace reader backed by the shared PostgreSQL pool."""

        self._repository = TraceRepository(pool)

    def get_query_result(self, query_trace_id: str) -> Mapping[str, Any] | None:
        """Return one trace's public query result payload.

        Args:
            query_trace_id: Trace ID from ``message_query_trace``.

        Returns:
            The immutable ``query_result`` mapping, or ``None`` if the trace row
            is missing.
        """

        trace = self._repository.get_query_trace(
            _required_text(query_trace_id, field_name="query_trace_id")
        )
        return None if trace is None else trace.query_result


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse the local evaluation command-line interface.

    Args:
        argv: Optional command arguments excluding the executable. ``None``
            uses process arguments through ``argparse``.

    Returns:
        Parsed namespace containing dataset, collection, evaluator, and query
        execution options.
    """

    parser = argparse.ArgumentParser(
        description="Run RAG golden-set evaluation and persist metric history."
    )
    parser.add_argument(
        "--collection",
        help="Restrict evaluation to one collection. Defaults to settings or sample collection.",
    )
    parser.add_argument(
        "--golden-set",
        help="Path to the golden set JSON file. Defaults to settings.evaluation.golden_set_path.",
    )
    parser.add_argument(
        "--evaluator",
        default="ragas",
        help="Evaluator provider registered in EvaluatorFactory. Defaults to ragas.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=None,
        help="Final query result count. Defaults to settings.retrieval.final_top_k.",
    )
    parser.add_argument(
        "--no-rerank",
        action="store_true",
        help="Skip the configured reranker during evaluation queries.",
    )
    parser.add_argument(
        "--answer-source",
        choices=tuple(source.value for source in EvaluationAnswerSource),
        default=None,
        help="Answer source to evaluate. Defaults to settings.evaluation.answer_source.",
    )
    return parser.parse_args(argv)


def _write_stderr_progress(message: str) -> None:
    """Write progress output without forcing line breaks during refresh mode."""

    sys.stderr.write(message)
    sys.stderr.flush()


def run_evaluation_cli(
    argv: Sequence[str] | None = None,
    *,
    output: Any = print,
    error_output: Any | None = None,
) -> int:
    """Execute golden-set evaluation and return a process exit code.

    Args:
        argv: Optional CLI arguments excluding the executable.
        output: Writer receiving one JSON success document.
        error_output: Writer receiving readable failure messages. ``None``
            writes to stderr.

    Returns:
        ``0`` on success, otherwise ``1``.

    Side Effects:
        Loads local environment variables, opens PostgreSQL, runs real query
        pipeline calls, writes query traces, and persists evaluation run/result
        records.
    """

    args = parse_args(argv)
    write_error = error_output or _write_stderr_progress
    pool: PostgresPool | None = None
    reporter = EvaluationReporter(
        console=write_error,
        log_path=EVALUATION_LOG_PATH,
        refresh=True,
        heartbeat_interval_seconds=10.0,
    )
    active_step = "startup"
    active_sample: Mapping[str, Any] | None = None
    active_sample_index: int | None = None
    try:
        _load_local_environment()
        settings = load_settings()
        top_k = args.top_k or settings.retrieval.final_top_k
        if top_k <= 0:
            raise ValueError("--top-k must be greater than zero")
        golden_set_path = _resolve_golden_set_path(args.golden_set, settings=settings)
        dataset = _filter_dataset(
            load_golden_set(golden_set_path),
            collection=args.collection,
        )
        if not dataset:
            raise ValueError("Golden set contains no samples for the selected collection")
        collections = _dataset_collections(dataset)
        collection = args.collection or _evaluation_collection_id(collections)
        answer_source = _answer_source(args.answer_source, settings=settings)
        metric_names = enabled_generation_metrics(settings)
        run_id = f"eval-{uuid4().hex}"
        reporter.run_started(
            run_id=run_id,
            dataset_name=golden_set_path.stem,
            sample_count=len(dataset),
            answer_source=answer_source.value,
            top_k=top_k,
            no_rerank=args.no_rerank,
            evaluator=args.evaluator,
            metric_names=metric_names,
            collections=collections,
        )

        active_step = "database_setup"
        reporter.step_started(active_step)
        pool = _create_pool(settings.database)
        pool.open()
        init_schema(pool)
        reporter.step_done(active_step)
        active_step = "runtime_setup"
        reporter.step_started(active_step)
        runtime = (
            _build_runtime(settings, pool, args.no_rerank)
            if answer_source is EvaluationAnswerSource.RAG
            else None
        )
        chunk_lookup = VectorStoreFactory.create(settings=settings, pool=pool)
        aimodel_client = (
            AImodelEvaluationClient(settings.evaluation.aimodel)
            if answer_source is EvaluationAnswerSource.MESSAGE
            else None
        )
        message_repository = (
            MessageAnswerRepository(pool)
            if answer_source is EvaluationAnswerSource.MESSAGE
            else None
        )
        query_trace_repository = (
            QueryTraceResultRepository(pool)
            if answer_source is EvaluationAnswerSource.MESSAGE
            else None
        )
        reporter.step_done(active_step)
        active_step = "build_predictions"
        reporter.step_started(active_step)
        predictions = []
        for sample_index, sample in enumerate(dataset, start=1):
            active_sample = sample
            active_sample_index = sample_index
            reporter.sample_started(
                sample_index=sample_index,
                sample_count=len(dataset),
                sample=sample,
            )
            predictions.append(
                _prediction_for_sample(
                    sample,
                    runtime=runtime,
                    chunk_lookup=chunk_lookup,
                    collection=_collection_for_sample(sample, override=args.collection),
                    top_k=top_k,
                    no_rerank=args.no_rerank,
                    answer_source=answer_source,
                    aimodel_client=aimodel_client,
                    message_repository=message_repository,
                    query_trace_repository=query_trace_repository,
                    reporter=reporter,
                    sample_index=sample_index,
                )
            )
        active_sample = None
        active_sample_index = None
        reporter.step_done(active_step, details={"prediction_count": len(predictions)})

        active_step = "ragas_evaluation"
        reporter.step_started(active_step)
        detail = EvaluationService(pool).run_evaluation(
            collection_id=collection,
            evaluator=args.evaluator,
            dataset_name=golden_set_path.stem,
            dataset=dataset,
            predictions=predictions,
            evaluator_options={
                "settings": settings,
                "metric_names": metric_names,
                "ragas_observer": reporter.ragas_observer,
            },
            run_id=run_id,
            settings_snapshot={
                "answer_source": answer_source.value,
                "collection": collection,
                "collections": collections,
                "top_k": top_k,
                "no_rerank": args.no_rerank,
                "golden_set_path": str(golden_set_path),
                "evaluation_llm_provider": settings.evaluation.llm_provider,
                "evaluation_embedding_provider": (
                    settings.evaluation.embedding_provider
                ),
                "generation_metrics": metric_names,
                "response_optimizer_enabled": (
                    settings.response.evidence_context_optimizer.enabled
                ),
            },
        )
        reporter.step_done(active_step)
        reporter.completed(detail=detail)
        output(json.dumps(_detail_payload(detail), ensure_ascii=False))
        return 0 if detail.status == "success" else 1
    except Exception as error:
        reporter.failed(
            step=active_step,
            error=error,
            sample=active_sample,
            sample_index=active_sample_index,
        )
        write_error(f"Evaluation failed: {error}")
        return 1
    finally:
        if pool is not None:
            pool.close()


def load_golden_set(path: Path) -> list[dict[str, Any]]:
    """Load and validate the JSON golden set used by evaluation.

    Args:
        path: Absolute golden set path.

    Returns:
        List of sample mappings.

    Raises:
        ValueError: If the file is missing, not a JSON array, or lacks required
            sample fields.
    """

    if not path.is_file():
        raise ValueError(f"Golden set file does not exist: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError("Golden set must be a non-empty JSON array")
    samples: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, Mapping):
            raise ValueError("Golden set samples must be JSON objects")
        sample = dict(item)
        for field_name in ("id", "collection", "question", "golden_answer"):
            _required_text(sample.get(field_name), field_name=field_name)
        samples.append(sample)
    return samples


def build_prediction_from_query_result(
    sample: Mapping[str, Any],
    query_result: Mapping[str, Any],
    *,
    chunk_lookup: ChunkLookup,
    query_trace_id: str,
    effective_collection: str | None = None,
) -> dict[str, Any]:
    """Build one Ragas-compatible prediction from a Query Trace result.

    Args:
        sample: Golden set sample that produced the query.
        query_result: Query trace ``query_result`` section containing content
            and ranked context identities.
        chunk_lookup: Vector store or fake exposing ``get_by_ids``.
        query_trace_id: Trace ID written by the query pipeline.
        effective_collection: Collection actually used for this sample. ``None``
            records the sample's own collection.

    Returns:
        Prediction record with ``answer`` from ``query_result.content`` and
        ``contexts`` from ranked chunk text.

    Raises:
        ValueError: If content, contexts, or looked-up chunk text is missing.
    """

    answer = _required_text(query_result.get("content"), field_name="query_result.content")
    ranked_contexts = _ranked_contexts(query_result.get("contexts"))
    chunk_ids = [context["chunk_id"] for context in ranked_contexts]
    chunks_by_id = {chunk.id: chunk for chunk in chunk_lookup.get_by_ids(chunk_ids)}
    missing = [chunk_id for chunk_id in chunk_ids if chunk_id not in chunks_by_id]
    if missing:
        raise ValueError(f"Unable to load retrieved chunk text for IDs: {missing}")
    retrieved_contexts = [
        _required_text(chunks_by_id[chunk_id].text, field_name=f"chunk {chunk_id} text")
        for chunk_id in chunk_ids
    ]
    return {
        "sample_id": sample.get("id"),
        "question": _required_text(sample.get("question"), field_name="question"),
        "answer": answer,
        "answer_source": EvaluationAnswerSource.RAG.value,
        "contexts": retrieved_contexts,
        "retrieved_contexts": retrieved_contexts,
        "query_trace_id": _required_text(query_trace_id, field_name="query_trace_id"),
        "context_chunk_ids": chunk_ids,
        "sample_collection": sample.get("collection"),
        "effective_collection": effective_collection or sample.get("collection"),
    }


def build_prediction_from_message_answer(
    sample: Mapping[str, Any],
    query_result: Mapping[str, Any],
    *,
    chunk_lookup: ChunkLookup,
    query_trace_id: str,
    message_answer: MessageAnswer,
    effective_collection: str | None = None,
) -> dict[str, Any]:
    """Build one Ragas prediction from a stored AImodel assistant message.

    Args:
        sample: Golden set sample that produced the query.
        query_result: Query result identity payload used only to resolve ranked
            retrieved contexts. Its ``content`` remains the RAG context package
            and is intentionally not used as the generated answer.
        chunk_lookup: Vector store or fake exposing ``get_by_ids``.
        query_trace_id: Trace ID written by the query pipeline and associated
            with the final assistant message through ``message_query_trace``.
        message_answer: Stored AImodel assistant answer resolved by trace ID.
        effective_collection: Collection actually used for this sample.

    Returns:
        Prediction record with ``answer`` from ``message.content`` and
        ``contexts`` from ranked chunk text.

    Raises:
        ValueError: If message content, contexts, or looked-up chunk text is
            missing.
    """

    prediction = build_prediction_from_query_result(
        sample,
        query_result,
        chunk_lookup=chunk_lookup,
        query_trace_id=query_trace_id,
        effective_collection=effective_collection,
    )
    prediction.update(
        {
            "answer": _required_text(
                message_answer.content,
                field_name="message.content",
            ),
            "answer_source": EvaluationAnswerSource.MESSAGE.value,
            "message_id": message_answer.message_id,
            "conversation_id": message_answer.conversation_id,
            "query_trace_ids": list(message_answer.query_trace_ids),
        }
    )
    return prediction


def main() -> int:
    """Run the evaluation CLI with process arguments."""

    return run_evaluation_cli()


def _prediction_for_sample(
    sample: Mapping[str, Any],
    *,
    runtime: Any | None,
    chunk_lookup: ChunkLookup,
    collection: str,
    top_k: int,
    no_rerank: bool,
    answer_source: EvaluationAnswerSource,
    aimodel_client: AImodelEvaluationClient | None,
    message_repository: MessageAnswerRepository | None,
    query_trace_repository: QueryTraceResultRepository | None,
    reporter: EvaluationReporter | None = None,
    sample_index: int | None = None,
) -> dict[str, Any]:
    """Execute one query and convert its result into a prediction record.

    Args:
        sample: Golden set sample containing the user question.
        runtime: QueryRuntime-compatible object used to execute the RAG query.
        chunk_lookup: Store used to resolve retrieved chunk text.
        collection: Collection selected for this evaluation run.
        top_k: Final result count requested from the query pipeline.
        no_rerank: Whether the query pipeline skips reranking.
        answer_source: Configured source for the Ragas ``answer`` field.
        aimodel_client: Required only for ``message`` mode to trigger AImodel.
        message_repository: Required only for ``message`` mode to resolve the
            stored assistant message by query trace ID.

    Returns:
        One Ragas-compatible prediction row.

    Raises:
        ValueError: If message mode lacks required collaborators or no stored
            assistant message can be found for the query trace.
    """

    question = _required_text(sample.get("question"), field_name="question")
    if answer_source is EvaluationAnswerSource.RAG:
        if runtime is None:
            raise ValueError("rag answer source requires QueryRuntime")
        trace_id = f"query-eval-{uuid4().hex}"
        execution = runtime.execute(
            question,
            collection=collection,
            top_k=top_k,
            no_rerank=no_rerank,
            trace_id=trace_id,
        )
        _report_sample_step(
            reporter,
            sample=sample,
            sample_index=sample_index,
            step="query_runtime",
            details={"query_trace_id": trace_id},
        )
        prediction = build_prediction_from_query_result(
            sample,
            _query_result_from_execution(execution),
            chunk_lookup=chunk_lookup,
            query_trace_id=trace_id,
            effective_collection=collection,
        )
        _report_sample_step(
            reporter,
            sample=sample,
            sample_index=sample_index,
            step="chunk_lookup",
            details={"context_count": len(prediction["contexts"])},
        )
        _report_sample_step(
            reporter,
            sample=sample,
            sample_index=sample_index,
            step="prediction_ready",
            details={"query_trace_id": prediction["query_trace_id"]},
        )
        return prediction
    if (
        aimodel_client is None
        or message_repository is None
        or query_trace_repository is None
    ):
        raise ValueError("message answer source requires AImodel and trace repositories")
    chat_result = aimodel_client.chat(question)
    _report_sample_step(
        reporter,
        sample=sample,
        sample_index=sample_index,
        step="aimodel_chat",
        details={"conversation_id": chat_result.get("conversation_id")},
    )
    message_answer = message_repository.get_answer_from_chat_result(chat_result)
    if message_answer is None:
        conversation_id = chat_result.get("conversation_id")
        raise ValueError(
            f"No assistant message found for AImodel conversation_id={conversation_id}"
        )
    if not message_answer.query_trace_ids:
        raise ValueError(
            f"No RAG query traces linked to message_id={message_answer.message_id}"
        )
    _report_sample_step(
        reporter,
        sample=sample,
        sample_index=sample_index,
        step="message_resolve",
        details={
            "message_id": message_answer.message_id,
            "conversation_id": message_answer.conversation_id,
            "query_trace_ids": list(message_answer.query_trace_ids),
        },
    )
    query_results = [
        _required_query_result(query_trace_repository, query_trace_id)
        for query_trace_id in message_answer.query_trace_ids
    ]
    _report_sample_step(
        reporter,
        sample=sample,
        sample_index=sample_index,
        step="query_trace_load",
        details={"query_trace_ids": list(message_answer.query_trace_ids)},
    )
    primary_trace_id = message_answer.query_trace_ids[0]
    prediction = build_prediction_from_message_answer(
        sample,
        _merge_query_results(query_results),
        chunk_lookup=chunk_lookup,
        query_trace_id=primary_trace_id,
        message_answer=message_answer,
        effective_collection=collection,
    )
    _report_sample_step(
        reporter,
        sample=sample,
        sample_index=sample_index,
        step="chunk_lookup",
        details={"context_count": len(prediction["contexts"])},
    )
    _report_sample_step(
        reporter,
        sample=sample,
        sample_index=sample_index,
        step="prediction_ready",
        details={
            "message_id": message_answer.message_id,
            "query_trace_id": primary_trace_id,
            "context_count": len(prediction["contexts"]),
        },
    )
    return prediction


def _query_result_from_execution(execution: Any) -> dict[str, Any]:
    """Convert ``QueryRuntime.execute`` output into a query_result-like mapping.

    Args:
        execution: Runtime result containing final retrieval results and the
            assembled ``KnowledgeHubResponse``.

    Returns:
        Public query result shape used by evaluation and Query Trace records.
    """

    return {
        "contexts": [
            {"chunk_id": result.chunk_id, "score": result.score, "rank": rank}
            for rank, result in enumerate(execution.final_results, start=1)
        ],
        "content": execution.response.content,
        "citations": [
            citation.model_dump(mode="json")
            for citation in execution.response.citations
        ],
        "images": [image.model_dump(mode="json") for image in execution.response.images],
    }


def _required_query_result(
    repository: QueryTraceResultRepository,
    query_trace_id: str,
) -> Mapping[str, Any]:
    """Load one linked query result or raise an actionable error."""

    query_result = repository.get_query_result(query_trace_id)
    if query_result is None:
        raise ValueError(f"No query trace found for query_trace_id={query_trace_id}")
    return query_result


def _merge_query_results(query_results: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Merge one or more AImodel-linked query results for one Ragas sample.

    AImodel can call the RAG tool more than once for a single user question.
    Ragas accepts one answer with many retrieved contexts, so this helper
    flattens trace contexts, de-duplicates chunk IDs in first-seen order, and
    assigns a fresh combined rank sequence.
    """

    merged_contexts: list[dict[str, Any]] = []
    seen_chunk_ids: set[str] = set()
    for query_result in query_results:
        for context in _ranked_contexts(query_result.get("contexts")):
            chunk_id = context["chunk_id"]
            if chunk_id in seen_chunk_ids:
                continue
            seen_chunk_ids.add(chunk_id)
            merged_contexts.append({"chunk_id": chunk_id, "rank": len(merged_contexts) + 1})
    if not merged_contexts:
        raise ValueError("Linked query traces contain no retrieved contexts")
    content_parts = [
        _required_text(query_result.get("content"), field_name="query_result.content")
        for query_result in query_results
    ]
    return {
        "contexts": merged_contexts,
        "content": "\n\n".join(content_parts),
        "citations": [
            citation
            for query_result in query_results
            for citation in _sequence_items(query_result.get("citations"))
        ],
        "images": [
            image
            for query_result in query_results
            for image in _sequence_items(query_result.get("images"))
        ],
    }


def _report_sample_step(
    reporter: EvaluationReporter | None,
    *,
    sample: Mapping[str, Any],
    sample_index: int | None,
    step: str,
    details: Mapping[str, Any] | None = None,
) -> None:
    """Emit one sample-level progress event when a reporter is active.

    Args:
        reporter: Optional reporter injected by ``run_evaluation_cli``.
        sample: Golden set sample associated with the event.
        sample_index: One-based sample index. ``None`` is accepted for direct
            unit calls that bypass the CLI loop.
        step: Stable sub-step name written to the JSONL diagnostics log.
        details: Optional small payload such as message IDs or trace IDs.
    """

    if reporter is None:
        return
    reporter.sample_step_done(
        sample_index=sample_index or 0,
        sample=sample,
        step=step,
        details=details,
    )



def _sample_step_status_text(
    *,
    sample_index: int,
    sample_count: int | None,
    sample: Mapping[str, Any],
    step: str,
    status: str,
    details: Mapping[str, Any],
) -> str:
    """Build a compact console line for one build_predictions sub-step."""

    sample_total = sample_count if sample_count is not None else "?"
    sample_id = _sample_id(sample)
    detail_keys = []
    for key in ("conversation_id", "message_id", "context_count"):
        if key in details:
            detail_keys.append(f"{key}={details[key]}")
    detail_text = f" {' '.join(detail_keys)}" if detail_keys else ""
    return f"[{sample_index}/{sample_total}] {sample_id} {step} {status}{detail_text}"

def _sample_id(sample: Mapping[str, Any]) -> str:
    """Return a readable sample identifier for progress and diagnostics."""

    value = sample.get("id")
    return str(value).strip() if value is not None and str(value).strip() else "unknown"


def _preview_text(value: str, *, max_length: int = 80) -> str:
    """Return a single-line preview that keeps progress output compact."""

    normalized = " ".join(value.split())
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[: max_length - 3]}..."


def _ragas_status_text(event: Mapping[str, Any]) -> str:
    """Build a detailed console status from a Ragas model-call event."""

    step = str(event.get("step", "ragas_model"))
    status = str(event.get("status", "unknown"))
    parts = [step, status, f"call_id={event.get('call_id', '')}"]
    _append_event_field(parts, event, "method")
    duration = event.get("duration_ms")
    if isinstance(duration, int | float):
        parts.append(f"duration={float(duration) / 1000:.1f}s")
    for key in (
        "provider",
        "model",
        "prompt_chars",
        "n",
        "temperature",
        "has_stop",
        "text_count",
        "total_chars",
        "output_chars",
        "vector_count",
        "dimension",
        "error_type",
    ):
        _append_event_field(parts, event, key)
    return " ".join(parts)


def _append_event_field(parts: list[str], event: Mapping[str, Any], key: str) -> None:
    """Append one trace-safe field to a console status line when present."""

    value = event.get(key)
    if value is not None:
        parts.append(f"{key}={value}")

def _answer_source(value: str | None, *, settings: RagSettings) -> EvaluationAnswerSource:
    """Resolve CLI and configuration into a concrete answer-source enum.

    Args:
        value: Optional CLI override from ``--answer-source``.
        settings: Loaded RAG settings containing the default evaluation source.

    Returns:
        Selected ``EvaluationAnswerSource``.

    Raises:
        ValueError: If the value is not one of the supported enum members.
    """

    candidate = value or settings.evaluation.answer_source
    try:
        return EvaluationAnswerSource(candidate)
    except ValueError as error:
        allowed = ", ".join(source.value for source in EvaluationAnswerSource)
        raise ValueError(
            f"Unsupported answer source '{candidate}'. Expected one of: {allowed}"
        ) from error


def _done_payload_from_sse(body: str) -> dict[str, Any]:
    """Extract the final ``done`` event payload from an AImodel SSE response.

    Args:
        body: Complete text/event-stream response body returned by FastAPI.

    Returns:
        JSON object from the last ``event: done`` block.

    Raises:
        ValueError: If the stream contains an error event, malformed JSON, or no
            done event.
    """

    done_payload: dict[str, Any] | None = None
    for event_name, payload in _iter_sse_events(body):
        if event_name == "error":
            message = payload.get("content") or payload.get("message") or payload
            raise ValueError(f"AImodel chat failed: {message}")
        if event_name == "done":
            done_payload = payload
    if done_payload is None:
        raise ValueError("AImodel chat did not return a done event")
    return done_payload


def _iter_sse_events(body: str) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield parsed SSE event names and JSON payloads from a response body."""

    event_name = "message"
    data_lines: list[str] = []
    for raw_line in body.splitlines() + [""]:
        line = raw_line.strip("\r")
        if not line:
            if data_lines:
                data_text = "\n".join(data_lines)
                try:
                    payload = json.loads(data_text)
                except json.JSONDecodeError as error:
                    raise ValueError("AImodel chat returned malformed SSE JSON") from error
                if not isinstance(payload, dict):
                    raise ValueError("AImodel chat SSE payload must be a JSON object")
                yield event_name, payload
            event_name = "message"
            data_lines = []
            continue
        if line.startswith("event:"):
            event_name = line.removeprefix("event:").strip()
        elif line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").strip())


def _resolve_golden_set_path(path: str | None, *, settings: RagSettings) -> Path:
    """Resolve configured or CLI-provided golden set paths."""

    candidate = Path(path or settings.evaluation.golden_set_path)
    if candidate.is_absolute():
        return candidate
    working_directory_candidate = (Path.cwd() / candidate).resolve()
    if working_directory_candidate.exists():
        return working_directory_candidate
    return (RAG_ROOT / candidate).resolve()


def _dataset_collections(dataset: Sequence[Mapping[str, Any]]) -> list[str]:
    """Return sorted collections represented by the validated dataset."""

    return sorted(
        {
            _required_text(sample.get("collection"), field_name="collection")
            for sample in dataset
        }
    )


def _evaluation_collection_id(collections: Sequence[str]) -> str:
    """Return the persisted evaluation run collection identifier."""

    if len(collections) == 1:
        return collections[0]
    return "mixed"


def _collection_for_sample(
    sample: Mapping[str, Any],
    *,
    override: str | None,
) -> str:
    """Resolve the collection used to evaluate one golden-set sample."""

    if override is not None:
        return _required_text(override, field_name="collection")
    return _required_text(sample.get("collection"), field_name="collection")


def _filter_dataset(
    dataset: list[dict[str, Any]],
    *,
    collection: str | None,
) -> list[dict[str, Any]]:
    """Return samples matching the selected collection, preserving order."""

    if collection is None:
        return dataset
    return [sample for sample in dataset if sample.get("collection") == collection]


def select_single_collection(dataset: Sequence[Mapping[str, Any]]) -> str:
    """Return the only collection represented by a dataset.

    Args:
        dataset: Already validated and optionally filtered golden samples.

    Returns:
        The single collection shared by all samples.

    Raises:
        ValueError: If the dataset mixes collections and the caller did not
            select one explicitly.
    """

    collections = sorted(
        {
            _required_text(sample.get("collection"), field_name="collection")
            for sample in dataset
        }
    )
    if len(collections) != 1:
        raise ValueError(
            "Golden set contains multiple collections; pass --collection to select one"
        )
    return collections[0]


def _ranked_contexts(value: Any) -> list[dict[str, Any]]:
    """Validate and sort ``query_result.contexts`` by final rank."""

    if not isinstance(value, Sequence) or isinstance(value, str) or not value:
        raise ValueError("query_result.contexts must be a non-empty list")
    contexts: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise ValueError("query_result.contexts items must be objects")
        chunk_id = _required_text(item.get("chunk_id"), field_name="context.chunk_id")
        rank = item.get("rank")
        if not isinstance(rank, int) or rank <= 0:
            raise ValueError("context.rank must be a positive integer")
        contexts.append({"chunk_id": chunk_id, "rank": rank})
    return sorted(contexts, key=lambda context: context["rank"])



def _sequence_items(value: Any) -> list[Any]:
    """Return list items for optional JSON arrays, rejecting scalar values."""

    if value is None:
        return []
    if isinstance(value, Sequence) and not isinstance(value, str):
        return list(value)
    raise ValueError("query_result optional arrays must be lists")


def _required_positive_int(value: Any, *, field_name: str) -> int:
    """Return a positive integer or raise a field-specific error."""

    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a positive integer")
    try:
        candidate = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must be a positive integer") from error
    if candidate <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return candidate

def _detail_payload(detail: Any) -> dict[str, Any]:
    """Convert an EvaluationRunDetail DTO into JSON-safe CLI output."""

    return {
        "run_id": detail.run_id,
        "collection": detail.collection_id,
        "evaluator": detail.evaluator,
        "dataset_name": detail.dataset_name,
        "status": detail.status,
        "metrics": dict(detail.metrics),
        "summary": dict(detail.summary),
        "error": dict(detail.error) if detail.error is not None else None,
    }


def _required_text(value: Any, *, field_name: str) -> str:
    """Return stripped non-empty text or raise a field-specific error."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()


if __name__ == "__main__":
    raise SystemExit(main())
