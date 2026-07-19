"""Run JD URL discovery, Yingdao capture, and pandas as one file pipeline.

The pipeline owns batch locking, resumable file handoffs, stable exit codes,
and the final result JSON. It delegates live browsing, RPA execution, and
dataset normalization to their concrete modules and never writes a database.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path

from data_ops.cli import process_batch
from data_ops.core.batch_manifest import BatchManifest, load_batch_manifest
from data_ops.discovery.jd_product_urls import (
    DiscoveryResult,
    JdProductDiscoveryError,
    discover_jd_product_urls,
)
from data_ops.orchestration.yingdao_runner import (
    CaptureRequest,
    YingdaoApiRunner,
    YingdaoCommandRunner,
    YingdaoRunner,
)
from data_ops.processors.jd_product import register_jd_product_processor
from data_ops.processors.jd_product_contract import JD_PRODUCT_DATASET_CONTRACT
from data_ops.processors.registry import UnknownDatasetTypeError, get_processor

_SAFE_BATCH_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class PipelineExitCode(IntEnum):
    """Define the stable process contract for J9 callers."""

    SUCCESS = 0
    PARTIAL_SUCCESS = 10
    DISCOVERY_FAILED = 20
    YINGDAO_FAILED = 30
    PANDAS_FAILED = 40


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Represent one terminal pipeline result and its artifact references."""

    status: str
    exit_code: PipelineExitCode
    batch_id: str
    discovered_count: int
    captured_count: int
    normalized_count: int
    failed_count: int
    result_path: Path
    paths: Mapping[str, str]
    error_code: str = ""
    retry_stage: str = ""

    def to_dict(self) -> dict[str, object]:
        """Return the stable JSON payload consumed by scripts and operators."""

        return {
            "status": self.status,
            "exit_code": int(self.exit_code),
            "batch_id": self.batch_id,
            "counts": {
                "discovered": self.discovered_count,
                "captured": self.captured_count,
                "normalized": self.normalized_count,
                "failed": self.failed_count,
            },
            "paths": dict(self.paths),
            "error_code": self.error_code,
            "retry_stage": self.retry_stage,
        }


DiscoveryCallable = Callable[..., DiscoveryResult]
ProcessorCallable = Callable[[str, Path, Path], BatchManifest]


def write_pipeline_result(result: PipelineResult) -> None:
    """Atomically write one result JSON without captured row contents."""

    result.result_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{result.result_path.name}.",
        suffix=".tmp",
        dir=result.result_path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as target:
            json.dump(
                result.to_dict(),
                target,
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary_path, result.result_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def _read_discovery_csv(path: Path) -> DiscoveryResult:
    """Load and validate a completed discovery handoff for resume."""

    with path.open("r", encoding="utf-8-sig", newline="") as source:
        rows = list(csv.DictReader(source))
    expected_indexes = [str(index) for index in range(1, len(rows) + 1)]
    indexes = [str(row.get("input_index", "")).strip() for row in rows]
    product_urls = tuple(str(row.get("product_url", "")).strip() for row in rows)
    if not product_urls or indexes != expected_indexes or any(not url for url in product_urls):
        raise JdProductDiscoveryError(
            "invalid_discovery_handoff",
            "existing URL CSV does not satisfy the input contract",
        )
    if len(product_urls) != len(set(product_urls)):
        raise JdProductDiscoveryError(
            "invalid_discovery_handoff",
            "existing URL CSV contains duplicate products",
        )
    return DiscoveryResult(output_path=path, product_urls=product_urls, pages_visited=0)


def _count_csv_rows(path: Path) -> int:
    """Count data rows without retaining captured contents."""

    with path.open("r", encoding="utf-8-sig", newline="") as source:
        return sum(1 for _ in csv.DictReader(source))


def _ensure_jd_processor() -> None:
    """Idempotently register the concrete processor at the app boundary."""

    try:
        get_processor("jd_product")
    except UnknownDatasetTypeError:
        register_jd_product_processor()


def _artifact_paths(root: Path, batch_id: str) -> dict[str, Path]:
    """Return every stable J9 path for one batch."""

    values = {"dataset_type": "jd_product", "batch_id": batch_id}
    archive_root = root / "archive" / "jd_product" / batch_id
    return {
        "input_csv": root / "discovery" / f"jd_product_urls_{batch_id}.csv",
        "raw_csv": root / "inbox" / f"jd_product_{batch_id}_raw.csv",
        "normalized_csv": root
        / "normalized"
        / JD_PRODUCT_DATASET_CONTRACT.normalized_filename_template.format(**values),
        "failed_csv": root
        / "normalized"
        / JD_PRODUCT_DATASET_CONTRACT.failed_filename_template.format(**values),
        "manifest": archive_root / "manifest.json",
        "archived_raw_csv": archive_root / f"jd_product_{batch_id}_raw.csv",
        "result": root / "results" / batch_id / "pipeline_result.json",
        "lock": root / "locks" / f"{batch_id}.lock",
    }


def _build_result(
    *,
    batch_id: str,
    artifacts: Mapping[str, Path],
    status: str,
    exit_code: PipelineExitCode,
    discovered_count: int = 0,
    captured_count: int = 0,
    normalized_count: int = 0,
    failed_count: int = 0,
    error_code: str = "",
    retry_stage: str = "",
) -> PipelineResult:
    """Build and publish one terminal result with existing artifact paths."""

    visible_paths = {
        name: str(path.resolve())
        for name, path in artifacts.items()
        if name != "lock" and (path.exists() or name in {"input_csv", "result"})
    }
    result = PipelineResult(
        status=status,
        exit_code=exit_code,
        batch_id=batch_id,
        discovered_count=discovered_count,
        captured_count=captured_count,
        normalized_count=normalized_count,
        failed_count=failed_count,
        result_path=artifacts["result"],
        paths=visible_paths,
        error_code=error_code,
        retry_stage=retry_stage,
    )
    write_pipeline_result(result)
    return result


def _result_from_manifest(
    batch_id: str,
    artifacts: Mapping[str, Path],
    manifest: BatchManifest,
    discovered_count: int,
) -> PipelineResult:
    """Convert a successful pandas manifest into success or partial success."""

    captured = int(manifest.row_counts.get("input_rows", 0))
    normalized = int(manifest.row_counts.get("normalized_rows", 0))
    failed = int(manifest.row_counts.get("failed_rows", 0))
    partial = failed > 0
    return _build_result(
        batch_id=batch_id,
        artifacts=artifacts,
        status="partial" if partial else "success",
        exit_code=(PipelineExitCode.PARTIAL_SUCCESS if partial else PipelineExitCode.SUCCESS),
        discovered_count=discovered_count,
        captured_count=captured,
        normalized_count=normalized,
        failed_count=failed,
    )


def run_jd_product_pipeline(
    *,
    batch_id: str,
    output_root: str | Path,
    runner: YingdaoRunner | None,
    max_pages: int,
    max_items: int,
    keyword: str | None = None,
    seed_url: str | None = None,
    browser_channel: str = "msedge",
    browser_executable: str | Path | None = None,
    browser_storage_state: str | Path | None = None,
    headless: bool = True,
    discoverer: DiscoveryCallable = discover_jd_product_urls,
    processor: ProcessorCallable = process_batch,
) -> PipelineResult:
    """Execute or resume one JD file pipeline and return a stable exit result.

    Side Effects:
        Visits real JD pages when discovery is incomplete, starts Yingdao when
        no raw CSV exists, writes normalized/archive files, and publishes a
        result JSON. No database or business API is used.
    """

    if _SAFE_BATCH_ID_PATTERN.fullmatch(batch_id) is None:
        raise ValueError("batch_id must use safe filename characters")
    root = Path(output_root).resolve()
    artifacts = _artifact_paths(root, batch_id)
    artifacts["lock"].parent.mkdir(parents=True, exist_ok=True)
    try:
        lock_descriptor = os.open(
            artifacts["lock"],
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
        )
    except FileExistsError:
        return _build_result(
            batch_id=batch_id,
            artifacts=artifacts,
            status="failed",
            exit_code=PipelineExitCode.YINGDAO_FAILED,
            error_code="batch_already_running",
            retry_stage="yingdao",
        )
    os.close(lock_descriptor)
    try:
        if artifacts["manifest"].is_file():
            manifest = load_batch_manifest(artifacts["manifest"])
            discovered_count = (
                len(_read_discovery_csv(artifacts["input_csv"]).product_urls)
                if artifacts["input_csv"].is_file()
                else int(manifest.row_counts.get("input_rows", 0))
            )
            return _result_from_manifest(
                batch_id,
                artifacts,
                manifest,
                discovered_count,
            )

        try:
            discovery = (
                _read_discovery_csv(artifacts["input_csv"])
                if artifacts["input_csv"].is_file()
                else discoverer(
                    keyword=keyword,
                    seed_url=seed_url,
                    output_path=artifacts["input_csv"],
                    max_pages=max_pages,
                    max_items=max_items,
                    browser_channel=browser_channel,
                    browser_executable=browser_executable,
                    storage_state=browser_storage_state,
                    headless=headless,
                )
            )
        except Exception as exc:
            error_code = getattr(exc, "code", "discovery_failed")
            return _build_result(
                batch_id=batch_id,
                artifacts=artifacts,
                status="failed",
                exit_code=PipelineExitCode.DISCOVERY_FAILED,
                error_code=str(error_code),
                retry_stage="discovery",
            )

        request = CaptureRequest(
            batch_id=batch_id,
            input_csv=discovery.output_path,
            raw_output_csv=artifacts["raw_csv"],
        )
        if not artifacts["raw_csv"].is_file():
            if runner is None:
                return _build_result(
                    batch_id=batch_id,
                    artifacts=artifacts,
                    status="failed",
                    exit_code=PipelineExitCode.YINGDAO_FAILED,
                    discovered_count=len(discovery.product_urls),
                    error_code="yingdao_runner_required",
                    retry_stage="yingdao",
                )
            try:
                handle = runner.start_capture(request)
                capture = runner.wait_for_capture(handle, request)
            except Exception as exc:
                return _build_result(
                    batch_id=batch_id,
                    artifacts=artifacts,
                    status="failed",
                    exit_code=PipelineExitCode.YINGDAO_FAILED,
                    discovered_count=len(discovery.product_urls),
                    error_code=f"yingdao_start_failed_{type(exc).__name__}",
                    retry_stage="yingdao",
                )
            if capture.status != "success" or not artifacts["raw_csv"].is_file():
                return _build_result(
                    batch_id=batch_id,
                    artifacts=artifacts,
                    status="failed",
                    exit_code=PipelineExitCode.YINGDAO_FAILED,
                    discovered_count=len(discovery.product_urls),
                    error_code=capture.error_code or "yingdao_output_missing",
                    retry_stage="yingdao",
                )

        captured_count = _count_csv_rows(artifacts["raw_csv"])
        try:
            _ensure_jd_processor()
            manifest = processor("jd_product", artifacts["raw_csv"], root)
        except Exception as exc:
            return _build_result(
                batch_id=batch_id,
                artifacts=artifacts,
                status="failed",
                exit_code=PipelineExitCode.PANDAS_FAILED,
                discovered_count=len(discovery.product_urls),
                captured_count=captured_count,
                error_code=f"pandas_processing_failed_{type(exc).__name__}",
                retry_stage="pandas",
            )
        return _result_from_manifest(
            batch_id,
            artifacts,
            manifest,
            len(discovery.product_urls),
        )
    finally:
        artifacts["lock"].unlink(missing_ok=True)


def _build_parser() -> argparse.ArgumentParser:
    """Build the one-command J9 interface."""

    parser = argparse.ArgumentParser(prog="talonmart-jd-pipeline")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--keyword")
    source.add_argument("--seed-url")
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--max-pages", type=int, required=True)
    parser.add_argument("--max-items", type=int, required=True)
    parser.add_argument("--browser-channel", default="msedge")
    parser.add_argument("--browser-executable", type=Path)
    parser.add_argument("--headful", action="store_true")
    parser.add_argument("--yingdao-mode", choices=("command", "api"), required=True)
    parser.add_argument("--yingdao-runner-path", type=Path)
    parser.add_argument("--yingdao-app-file", type=Path)
    parser.add_argument("--yingdao-account-name")
    parser.add_argument("--yingdao-robot-uuid")
    parser.add_argument("--yingdao-timeout", type=float, default=600.0)
    return parser


def _runner_from_args(args: argparse.Namespace) -> YingdaoRunner:
    """Construct one runner while keeping API secrets in environment variables."""

    if args.yingdao_mode == "command":
        if args.yingdao_runner_path is None or args.yingdao_app_file is None:
            raise ValueError("command mode requires runner path and exported app file")
        return YingdaoCommandRunner(
            runner_path=args.yingdao_runner_path,
            app_file=args.yingdao_app_file,
            timeout_seconds=args.yingdao_timeout,
        )
    access_key_id = os.environ.get("YINGDAO_ACCESS_KEY_ID", "")
    access_key_secret = os.environ.get("YINGDAO_ACCESS_KEY_SECRET", "")
    if not all(
        (
            access_key_id,
            access_key_secret,
            args.yingdao_account_name,
            args.yingdao_robot_uuid,
        )
    ):
        raise ValueError("api mode requires Yingdao credentials, account, and robot UUID")
    return YingdaoApiRunner(
        access_key_id=access_key_id,
        access_key_secret=access_key_secret,
        account_name=args.yingdao_account_name,
        robot_uuid=args.yingdao_robot_uuid,
        timeout_seconds=args.yingdao_timeout,
    )


def _can_resume_without_runner(args: argparse.Namespace) -> bool:
    """Return whether persisted handoffs make external capture unnecessary."""

    artifacts = _artifact_paths(Path(args.output_root).resolve(), args.batch_id)
    return artifacts["manifest"].is_file() or (
        artifacts["input_csv"].is_file() and artifacts["raw_csv"].is_file()
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Run the J9 CLI, print its result JSON, and return the stable exit code."""

    args = _build_parser().parse_args(argv)
    try:
        runner = None if _can_resume_without_runner(args) else _runner_from_args(args)
        result = run_jd_product_pipeline(
            batch_id=args.batch_id,
            output_root=args.output_root,
            runner=runner,
            keyword=args.keyword,
            seed_url=args.seed_url,
            max_pages=args.max_pages,
            max_items=args.max_items,
            browser_channel=args.browser_channel,
            browser_executable=args.browser_executable,
            browser_storage_state=os.environ.get("JD_PLAYWRIGHT_STORAGE_STATE"),
            headless=not args.headful,
        )
    except (OSError, ValueError) as exc:
        print(str(exc), file=os.sys.stderr)
        return int(PipelineExitCode.YINGDAO_FAILED)
    print(json.dumps(result.to_dict(), ensure_ascii=True, sort_keys=True))
    return int(result.exit_code)


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "PipelineExitCode",
    "PipelineResult",
    "main",
    "run_jd_product_pipeline",
    "write_pipeline_result",
]
