"""Protect the J9 file pipeline across discovery, Yingdao, and pandas.

The URL discovery module has a separate live-JD test. These orchestration
tests use a controlled runner because they validate batch transitions and
exit-code mapping rather than ShadowBot's external runtime availability.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from data_ops.discovery.jd_product_urls import DiscoveryResult
from data_ops.orchestration.jd_product_pipeline import (
    PipelineExitCode,
    main,
    run_jd_product_pipeline,
)
from data_ops.orchestration.yingdao_runner import (
    CaptureHandle,
    CaptureRequest,
    CaptureResult,
    YingdaoApiRunner,
    YingdaoCommandRunner,
)
from data_ops.processors import registry as processor_registry


@pytest.fixture(autouse=True)
def restore_processor_registry() -> Iterator[None]:
    """Keep J9's lazy processor registration isolated from older test modules."""

    original_factories = dict(processor_registry._PROCESSOR_FACTORIES)
    yield
    processor_registry._PROCESSOR_FACTORIES.clear()
    processor_registry._PROCESSOR_FACTORIES.update(original_factories)


def _write_raw_export(request: CaptureRequest, *, status: str = "success") -> None:
    """Write one RPA-shaped row for an injected Yingdao runner."""

    request.raw_output_csv.parent.mkdir(parents=True, exist_ok=True)
    with request.raw_output_csv.open("w", encoding="utf-8-sig", newline="") as target:
        writer = csv.DictWriter(
            target,
            fieldnames=(
                "dataset_type",
                "batch_id",
                "input_index",
                "source_url",
                "captured_at",
                "crawl_status",
                "error_code",
                "jd_sku_id",
                "title",
                "display_price",
                "shop_name",
                "primary_image_url",
                "capture_region",
            ),
        )
        writer.writeheader()
        writer.writerow(
            {
                "dataset_type": "jd_product",
                "batch_id": request.batch_id,
                "input_index": "1",
                "source_url": "https://item.jd.com/100040114484.html",
                "captured_at": "2026-07-19T00:00:00Z",
                "crawl_status": status,
                "error_code": "" if status == "success" else "field_missing",
                "jd_sku_id": "100040114484",
                "title": "Synthetic pipeline row",
                "display_price": "99.00",
                "shop_name": "Synthetic shop",
                "primary_image_url": "https://img.example.invalid/product.jpg",
                "capture_region": "",
            }
        )


class SuccessfulRunner:
    """Materialize one raw export and report a successful capture."""

    def start_capture(self, request: CaptureRequest) -> CaptureHandle:
        """Create the handoff file before returning the run identity."""

        _write_raw_export(request)
        return CaptureHandle(run_id="run-success")

    def wait_for_capture(
        self,
        handle: CaptureHandle,
        request: CaptureRequest,
    ) -> CaptureResult:
        """Return success for the raw export created during start."""

        return CaptureResult(run_id=handle.run_id, status="success")


class FailingRunner:
    """Represent a ShadowBot launch rejected before CSV creation."""

    def start_capture(self, request: CaptureRequest) -> CaptureHandle:
        """Return an accepted identity without creating an output."""

        return CaptureHandle(run_id="run-failed")

    def wait_for_capture(
        self,
        handle: CaptureHandle,
        request: CaptureRequest,
    ) -> CaptureResult:
        """Report the controlled Yingdao failure code."""

        return CaptureResult(
            run_id=handle.run_id,
            status="failed",
            error_code="yingdao_client_unavailable",
        )


class UnexpectedRunner:
    """Fail the test if a resumable batch tries to start Yingdao again."""

    def start_capture(self, request: CaptureRequest) -> CaptureHandle:
        """Reject an unexpected start call."""

        raise AssertionError("Yingdao must not restart for a resumable batch")

    def wait_for_capture(
        self,
        handle: CaptureHandle,
        request: CaptureRequest,
    ) -> CaptureResult:
        """Reject an unexpected wait call."""

        raise AssertionError("Yingdao must not be awaited for a resumable batch")


def _discover(output_path: Path, **kwargs: object) -> DiscoveryResult:
    """Write one deterministic URL input for orchestration-only tests."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "input_index,product_url\n1,https://item.jd.com/100040114484.html\n",
        encoding="utf-8-sig",
    )
    return DiscoveryResult(
        output_path=output_path,
        product_urls=("https://item.jd.com/100040114484.html",),
        pages_visited=1,
    )


def test_run_jd_product_pipeline_writes_reconciled_success_result(
    tmp_path: Path,
) -> None:
    """One command links discovery, RPA output, pandas, manifest, and result JSON."""

    result = run_jd_product_pipeline(
        batch_id="j9_success",
        output_root=tmp_path,
        runner=SuccessfulRunner(),
        seed_url="https://search.jd.com/Search?keyword=phone",
        max_pages=1,
        max_items=5,
        discoverer=_discover,
    )

    assert result.status == "success"
    assert result.exit_code == PipelineExitCode.SUCCESS
    assert result.discovered_count == 1
    assert result.captured_count == 1
    assert result.normalized_count == 1
    assert result.failed_count == 0
    payload = json.loads(result.result_path.read_text(encoding="utf-8"))
    assert payload["batch_id"] == "j9_success"
    assert payload["exit_code"] == 0
    assert Path(payload["paths"]["manifest"]).is_file()
    assert Path(payload["paths"]["normalized_csv"]).is_file()
    assert Path(payload["paths"]["failed_csv"]).is_file()


def test_run_jd_product_pipeline_maps_yingdao_failure_to_exit_30(
    tmp_path: Path,
) -> None:
    """A failed ShadowBot stage stops pandas and leaves a retryable result."""

    result = run_jd_product_pipeline(
        batch_id="j9_yingdao_failed",
        output_root=tmp_path,
        runner=FailingRunner(),
        keyword="phone",
        max_pages=1,
        max_items=5,
        discoverer=_discover,
    )

    assert result.status == "failed"
    assert result.exit_code == PipelineExitCode.YINGDAO_FAILED
    assert result.error_code == "yingdao_client_unavailable"
    assert result.retry_stage == "yingdao"
    assert result.result_path.is_file()
    assert not list((tmp_path / "archive").rglob("manifest.json"))


def test_yingdao_command_runner_passes_only_public_app_parameters(
    tmp_path: Path,
) -> None:
    """The local command exposes file handoffs but no account credentials."""

    runner_path = tmp_path / "ShadowBot.exe"
    app_file = tmp_path / "jd-product-export.shr"
    input_csv = tmp_path / "input.csv"
    for path in (runner_path, app_file, input_csv):
        path.write_bytes(b"")
    request = CaptureRequest(
        batch_id="j9_command",
        input_csv=input_csv,
        raw_output_csv=tmp_path / "raw.csv",
    )
    command = YingdaoCommandRunner(
        runner_path=runner_path,
        app_file=app_file,
    ).build_command(request)

    assert command[:3] == (
        str(runner_path.resolve()),
        "--mode=robot",
        f"--app-file={app_file.resolve()}",
    )
    parameters = json.loads(command[3].removeprefix("--app-params="))
    assert parameters == request.app_parameters()
    assert set(parameters) == {"batch_id", "input_csv", "raw_output_csv"}
    assert not any("secret" in value.lower() or "cookie" in value.lower() for value in command)


def test_yingdao_api_runner_queries_job_uuid_with_get(
    tmp_path: Path,
    monkeypatch: object,
) -> None:
    """Enterprise polling uses the accepted job UUID without placing secrets in URLs."""

    raw_output_csv = tmp_path / "raw.csv"
    raw_output_csv.write_bytes(b"dataset_type\n")
    request = CaptureRequest(
        batch_id="j9_api",
        input_csv=tmp_path / "input.csv",
        raw_output_csv=raw_output_csv,
    )
    calls: list[dict[str, object]] = []

    def fake_request_json(
        self: YingdaoApiRunner,
        method: str,
        path: str,
        **kwargs: object,
    ) -> dict[str, object]:
        calls.append({"method": method, "path": path, **kwargs})
        return {"code": 200, "success": True, "data": {"status": "finish"}}

    monkeypatch.setattr(YingdaoApiRunner, "_request_json", fake_request_json)
    runner = YingdaoApiRunner(
        access_key_id="not-sent",
        access_key_secret="not-sent",
        account_name="operator",
        robot_uuid="robot",
    )
    result = runner.wait_for_capture(CaptureHandle(run_id="job-123"), request)

    assert result.status == "success"
    assert calls == [
        {
            "method": "GET",
            "path": "/oapi/dispatch/v2/job/query",
            "query": {"jobUuid": "job-123"},
            "authorized": True,
        }
    ]


def test_run_jd_product_pipeline_rejects_an_existing_batch_lock(
    tmp_path: Path,
) -> None:
    """An active batch lock prevents a second Yingdao process from starting."""

    lock_path = tmp_path / "locks" / "j9_locked.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text("active\n", encoding="utf-8")

    result = run_jd_product_pipeline(
        batch_id="j9_locked",
        output_root=tmp_path,
        runner=UnexpectedRunner(),
        keyword="phone",
        max_pages=1,
        max_items=5,
        discoverer=_discover,
    )

    assert result.exit_code == PipelineExitCode.YINGDAO_FAILED
    assert result.error_code == "batch_already_running"
    assert lock_path.is_file()


def test_run_jd_product_pipeline_resumes_from_existing_raw_csv(
    tmp_path: Path,
) -> None:
    """A retry with URL and raw handoffs skips both live discovery and Yingdao."""

    batch_id = "j9_resume"
    input_csv = tmp_path / "discovery" / f"jd_product_urls_{batch_id}.csv"
    _discover(input_csv)
    raw_csv = tmp_path / "inbox" / f"jd_product_{batch_id}_raw.csv"
    _write_raw_export(
        CaptureRequest(
            batch_id=batch_id,
            input_csv=input_csv,
            raw_output_csv=raw_csv,
        )
    )

    result = run_jd_product_pipeline(
        batch_id=batch_id,
        output_root=tmp_path,
        runner=UnexpectedRunner(),
        keyword="phone",
        max_pages=1,
        max_items=5,
        discoverer=lambda **kwargs: (_ for _ in ()).throw(
            AssertionError("discovery must not restart")
        ),
    )

    assert result.exit_code == PipelineExitCode.SUCCESS
    assert result.discovered_count == 1
    assert result.captured_count == 1
    assert result.normalized_count == 1


def test_cli_resumes_existing_raw_csv_without_yingdao_configuration(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A pandas-only retry must not require an exported app or API credentials."""

    batch_id = "j9_cli_resume"
    input_csv = tmp_path / "discovery" / f"jd_product_urls_{batch_id}.csv"
    _discover(input_csv)
    raw_csv = tmp_path / "inbox" / f"jd_product_{batch_id}_raw.csv"
    _write_raw_export(
        CaptureRequest(
            batch_id=batch_id,
            input_csv=input_csv,
            raw_output_csv=raw_csv,
        )
    )

    exit_code = main(
        [
            "--seed-url",
            "https://search.jd.com/Search?keyword=phone",
            "--batch-id",
            batch_id,
            "--output-root",
            str(tmp_path),
            "--max-pages",
            "1",
            "--max-items",
            "5",
            "--yingdao-mode",
            "command",
        ]
    )

    assert exit_code == PipelineExitCode.SUCCESS
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "success"
    assert payload["counts"] == {
        "discovered": 1,
        "captured": 1,
        "normalized": 1,
        "failed": 0,
    }
