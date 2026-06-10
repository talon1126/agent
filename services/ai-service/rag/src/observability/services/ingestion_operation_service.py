"""Run Dashboard-triggered ingestion through the canonical ingest entry point.

The Dashboard should not duplicate the offline ingestion pipeline wiring. This
service is the small orchestration boundary between Streamlit controls and
``src.scripts.ingest.run_ingest_cli()``: it validates the operator request,
normalizes paths for local Dashboard usage, captures CLI output, and returns a
typed result that pages can render without parsing process-style text.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.core.config import RAG_ROOT
from src.scripts.ingest import run_ingest_cli

SUPPORTED_DASHBOARD_SOURCE_SUFFIXES = frozenset({".md", ".markdown", ".pdf"})
DEFAULT_UPLOAD_ROOT = RAG_ROOT / "data" / "raw"


@dataclass(frozen=True, slots=True)
class UploadedIngestionFile:
    """Carry one browser-uploaded file into the ingestion operation service.

    Args:
        filename: Browser-provided file name. Directory uploads may include a
            relative path such as ``folder/guide.pdf``.
        content: Raw file bytes read from Streamlit's ``UploadedFile`` object.
    """

    filename: str
    content: bytes


@dataclass(frozen=True, slots=True)
class IngestionOperationRequest:
    """Describe one ingestion request submitted from the Dashboard.

    Args:
        collection: Target collection selected by Dashboard context.
        source_path: File or directory path entered by the operator.
        force: Whether to bypass source-hash deduplication.
        source_paths: Explicit selected local file paths after candidate
            discovery and operator deselection.
        uploaded_files: Browser-uploaded files selected for ingestion.
    """

    collection: str
    source_path: str = ""
    force: bool = False
    source_paths: tuple[str, ...] = ()
    uploaded_files: tuple[UploadedIngestionFile, ...] = ()


@dataclass(frozen=True, slots=True)
class IngestionOperationResult:
    """Represent a Dashboard-safe summary of an ingestion execution.

    Args:
        status: One of ``success``, ``skipped``, or ``failed``.
        collection: Collection used for ingestion.
        source_path: Normalized source path passed to the ingest entry point.
        force: Whether force rebuild was requested.
        exit_code: Process-compatible code returned by the ingest runner.
        processed: Number of source documents reported by ingestion.
        trace_ids: Trace IDs returned by each processed source.
        source_paths: Source paths returned by each processed result.
        summary: Single-source summary or compact multi-source summary.
        error: Human-readable error text when ingestion failed.
        raw_output: Raw stdout lines captured from the ingest runner.
        raw_errors: Raw stderr-like lines captured from the ingest runner.
    """

    status: str
    collection: str
    source_path: str
    force: bool
    exit_code: int
    processed: int = 0
    trace_ids: tuple[str, ...] = ()
    source_paths: tuple[str, ...] = ()
    summary: Mapping[str, Any] = field(default_factory=dict)
    error: str | None = None
    raw_output: tuple[str, ...] = ()
    raw_errors: tuple[str, ...] = ()


class IngestionOperationService:
    """Execute ingestion requests submitted by Dashboard operators.

    The default runner calls ``run_ingest_cli()`` in-process so Dashboard and
    CLI share the same settings loading, provider factories, pipeline builder,
    trace writing, and PostgreSQL persistence behavior. Tests can inject a fake
    runner to avoid model calls and database writes.
    """

    def __init__(
        self,
        *,
        runner: Callable[..., int] | None = None,
        upload_root: str | Path = DEFAULT_UPLOAD_ROOT,
    ) -> None:
        """Create a Dashboard ingestion operation service.

        Args:
            runner: Optional ingest-compatible callable. It receives CLI argv
                plus ``output`` and ``error_output`` keyword writers. ``None``
                uses ``src.scripts.ingest.run_ingest_cli``.
            upload_root: Root directory for files uploaded through the
                Dashboard. Files are saved under
                ``{upload_root}/{collection}/dashboard_uploads``.
        """

        self._runner = runner or run_ingest_cli
        self._upload_root = Path(upload_root).expanduser().resolve()

    def run_ingestion(
        self,
        request: IngestionOperationRequest,
    ) -> IngestionOperationResult:
        """Run one ingestion request and return a typed Dashboard result.

        Args:
            request: Collection, source path, and force flag collected by the
                Ingestion Management page.

        Returns:
            Parsed ingestion result. Runner failures are converted into
            ``status='failed'`` instead of escaping into the Streamlit render
            loop.
        """

        try:
            collection = _require_non_blank(request.collection, field_name="collection")
            source_paths = self._selected_source_paths(request)
        except ValueError as error:
            return IngestionOperationResult(
                status="failed",
                collection=str(request.collection).strip(),
                source_path=str(request.source_path).strip(),
                force=request.force,
                exit_code=2,
                error=str(error),
            )

        results = [
            self._run_single_source(
                source_path,
                collection=collection,
                force=request.force,
            )
            for source_path in source_paths
        ]
        return _merge_results(
            results,
            collection=collection,
            source_path=str(request.source_path).strip(),
            force=request.force,
        )

    def discover_source_candidates(self, source_path: str) -> tuple[str, ...]:
        """Return supported files represented by a file or directory path.

        Args:
            source_path: Operator-entered local path. Relative ``data/...``
                values are resolved against the RAG module root.

        Returns:
            Sorted absolute file paths with suffixes supported by ingestion.
            Missing or unsupported paths return an empty tuple so the Dashboard
            can render an empty candidate list without failing the page.
        """

        try:
            resolved = _normalize_source_path(source_path)
        except ValueError:
            return ()
        if resolved.is_file():
            if resolved.suffix.lower() in SUPPORTED_DASHBOARD_SOURCE_SUFFIXES:
                return (str(resolved),)
            return ()
        if not resolved.is_dir():
            return ()
        return tuple(
            str(path.resolve())
            for path in sorted(resolved.rglob("*"))
            if path.is_file()
            and path.suffix.lower() in SUPPORTED_DASHBOARD_SOURCE_SUFFIXES
        )

    def _selected_source_paths(
        self,
        request: IngestionOperationRequest,
    ) -> tuple[Path, ...]:
        """Resolve selected local and uploaded files into ingestable paths."""

        source_paths: list[Path] = []
        if request.source_paths:
            source_paths.extend(_normalize_source_path(path) for path in request.source_paths)
        elif str(request.source_path).strip():
            source_paths.extend(
                Path(candidate)
                for candidate in self.discover_source_candidates(request.source_path)
            )
        source_paths.extend(
            self._save_uploaded_file(request.collection, uploaded_file)
            for uploaded_file in request.uploaded_files
        )
        if not source_paths:
            if str(request.source_path).strip() or request.uploaded_files:
                raise ValueError("No supported ingestion sources selected.")
            raise ValueError("source_path must not be blank")
        return tuple(source_paths)

    def _save_uploaded_file(
        self,
        collection: str,
        uploaded_file: UploadedIngestionFile,
    ) -> Path:
        """Persist one browser-uploaded file under the configured raw data root."""

        collection_dir = _safe_relative_path(collection)
        relative_file = _safe_relative_path(uploaded_file.filename)
        destination = (
            self._upload_root / collection_dir / "dashboard_uploads" / relative_file
        ).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(uploaded_file.content)
        return destination

    def _run_single_source(
        self,
        source_path: Path,
        *,
        collection: str,
        force: bool,
    ) -> IngestionOperationResult:
        """Invoke the canonical ingest runner for one selected source."""

        argv = ["--path", str(source_path), "--collection", collection]
        if force:
            argv.append("--force")

        output_lines: list[str] = []
        error_lines: list[str] = []
        try:
            exit_code = self._runner(
                argv,
                output=output_lines.append,
                error_output=error_lines.append,
            )
        except Exception as error:
            return IngestionOperationResult(
                status="failed",
                collection=collection,
                source_path=str(source_path),
                force=force,
                exit_code=1,
                error=str(error),
                raw_output=tuple(output_lines),
                raw_errors=tuple(error_lines),
            )

        payload = _parse_last_json_line(output_lines)
        if exit_code != 0:
            return IngestionOperationResult(
                status="failed",
                collection=collection,
                source_path=str(source_path),
                force=force,
                exit_code=exit_code,
                error=_join_error_lines(error_lines, output_lines),
                raw_output=tuple(output_lines),
                raw_errors=tuple(error_lines),
            )
        return _result_from_payload(
            payload,
            collection=collection,
            source_path=source_path,
            force=force,
            exit_code=exit_code,
            output_lines=output_lines,
            error_lines=error_lines,
        )


def _normalize_source_path(source_path: str) -> Path:
    """Normalize an operator-entered source path for local Dashboard execution.

    Relative ``data/...`` paths are resolved against the RAG module root because
    Dashboard settings are RAG-relative. Other relative paths prefer the shell
    working directory when that path exists, then fall back to RAG-relative
    resolution so ``ingest.py`` can produce its normal source-not-found error.
    """

    normalized = _require_non_blank(source_path, field_name="source_path")
    candidate = Path(normalized).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()

    rag_relative = (RAG_ROOT / candidate).resolve()
    cwd_relative = candidate.resolve()
    if str(candidate).replace("\\", "/").startswith("data/"):
        return rag_relative
    if cwd_relative.exists():
        return cwd_relative
    return rag_relative


def _require_non_blank(value: str, *, field_name: str) -> str:
    """Return a stripped string or raise ``ValueError`` for blank input."""

    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    return normalized


def _safe_relative_path(value: str) -> Path:
    """Return a relative path safe to append below the upload root."""

    normalized = _require_non_blank(value, field_name="filename").replace("\\", "/")
    parts = [
        part
        for part in Path(normalized).parts
        if part not in {"", ".", ".."} and not Path(part).is_absolute()
    ]
    if not parts:
        raise ValueError("filename must contain at least one safe path segment")
    return Path(*parts)


def _parse_last_json_line(output_lines: Sequence[str]) -> Mapping[str, Any]:
    """Parse the final JSON line emitted by ``run_ingest_cli``.

    Args:
        output_lines: Captured output lines from the ingest runner.

    Returns:
        Parsed mapping, or an empty mapping when the runner emitted no JSON.
    """

    for line in reversed(output_lines):
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, Mapping):
            return payload
    return {}


def _result_from_payload(
    payload: Mapping[str, Any],
    *,
    collection: str,
    source_path: Path,
    force: bool,
    exit_code: int,
    output_lines: Sequence[str],
    error_lines: Sequence[str],
) -> IngestionOperationResult:
    """Convert a successful ingest JSON payload into a Dashboard DTO."""

    results = payload.get("results", ())
    result_items = tuple(item for item in results if isinstance(item, Mapping))
    trace_ids = tuple(str(item["trace_id"]) for item in result_items if item.get("trace_id"))
    source_paths = tuple(str(item["source"]) for item in result_items if item.get("source"))
    processed = int(payload.get("processed", len(result_items)) or 0)
    statuses = {str(item.get("status", "")) for item in result_items}
    status = "skipped" if statuses and statuses <= {"skipped"} else "success"
    return IngestionOperationResult(
        status=status,
        collection=str(payload.get("collection") or collection),
        source_path=str(source_path),
        force=bool(payload.get("force", force)),
        exit_code=exit_code,
        processed=processed,
        trace_ids=trace_ids,
        source_paths=source_paths,
        summary=_summary_from_results(result_items),
        raw_output=tuple(output_lines),
        raw_errors=tuple(error_lines),
    )


def _merge_results(
    results: Sequence[IngestionOperationResult],
    *,
    collection: str,
    source_path: str,
    force: bool,
) -> IngestionOperationResult:
    """Merge one or more source-level ingestion results for Dashboard display."""

    if len(results) == 1:
        return results[0]
    failed = [result for result in results if result.status == "failed"]
    status = "failed" if failed else _merged_success_status(results)
    return IngestionOperationResult(
        status=status,
        collection=collection,
        source_path=source_path,
        force=force,
        exit_code=failed[0].exit_code if failed else 0,
        processed=sum(result.processed for result in results),
        trace_ids=tuple(trace_id for result in results for trace_id in result.trace_ids),
        source_paths=tuple(path for result in results for path in result.source_paths),
        summary={
            "results": [
                {
                    "status": result.status,
                    "processed": result.processed,
                    "trace_ids": list(result.trace_ids),
                    "source_paths": list(result.source_paths),
                    "summary": dict(result.summary),
                    "error": result.error,
                }
                for result in results
            ]
        },
        error=failed[0].error if failed else None,
        raw_output=tuple(line for result in results for line in result.raw_output),
        raw_errors=tuple(line for result in results for line in result.raw_errors),
    )


def _merged_success_status(results: Sequence[IngestionOperationResult]) -> str:
    """Return ``skipped`` only when every source-level result was skipped."""

    if results and all(result.status == "skipped" for result in results):
        return "skipped"
    return "success"


def _summary_from_results(results: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
    """Return a compact summary for one or many ingested sources."""

    if len(results) == 1:
        summary = results[0].get("summary", {})
        return summary if isinstance(summary, Mapping) else {}
    return {
        "results": [
            {
                "source": item.get("source"),
                "status": item.get("status"),
                "trace_id": item.get("trace_id"),
                "summary": item.get("summary", {}),
            }
            for item in results
        ]
    }


def _join_error_lines(
    error_lines: Sequence[str],
    output_lines: Sequence[str],
) -> str:
    """Join runner diagnostics into a single Dashboard-safe error string."""

    joined = "\n".join([*error_lines, *output_lines]).strip()
    return joined or "Ingestion failed without diagnostic output."
