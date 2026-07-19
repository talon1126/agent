"""Start Yingdao through its exported-app command or enterprise OpenAPI.

The runners receive only batch and file paths. Credentials stay in memory,
subprocesses are launched without a shell, and errors never include response
bodies or command parameters that could expose secrets.
"""

from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

CaptureStatus = Literal["success", "failed"]
_SUCCESS_STATUSES = frozenset({"success", "succeeded", "finish", "finished", "completed"})
_FAILURE_STATUSES = frozenset({"failed", "error", "stopped", "cancelled", "canceled"})


@dataclass(frozen=True, slots=True)
class CaptureRequest:
    """Describe one file-only handoff to the Yingdao application."""

    batch_id: str
    input_csv: Path
    raw_output_csv: Path

    def app_parameters(self) -> dict[str, str]:
        """Return the three public application parameters as absolute paths."""

        return {
            "batch_id": self.batch_id,
            "input_csv": str(self.input_csv.resolve()),
            "raw_output_csv": str(self.raw_output_csv.resolve()),
        }


@dataclass(frozen=True, slots=True)
class CaptureHandle:
    """Identify an accepted command process or OpenAPI job."""

    run_id: str


@dataclass(frozen=True, slots=True)
class CaptureResult:
    """Report the terminal capture state without returning captured rows."""

    run_id: str
    status: CaptureStatus
    error_code: str = ""


class YingdaoRunner(Protocol):
    """Define the two-step runner contract used by the pipeline."""

    def start_capture(self, request: CaptureRequest) -> CaptureHandle:
        """Accept one batch and return its external run identity."""

    def wait_for_capture(
        self,
        handle: CaptureHandle,
        request: CaptureRequest,
    ) -> CaptureResult:
        """Wait for a terminal state and verify the raw CSV handoff."""


@dataclass(slots=True)
class YingdaoCommandRunner:
    """Run a community/personal exported ``.shr`` application locally.

    The command follows Yingdao's exported-app convention and supplies one
    JSON ``--app-params`` value. The exported flow must read those parameters
    and write ``raw_output_csv`` before exiting successfully.
    """

    runner_path: Path
    app_file: Path
    timeout_seconds: float = 600.0
    _processes: dict[str, subprocess.Popen[str]] = field(default_factory=dict)

    def build_command(self, request: CaptureRequest) -> tuple[str, ...]:
        """Build a shell-free ShadowBot command with no credentials."""

        parameters = json.dumps(
            request.app_parameters(),
            ensure_ascii=True,
            separators=(",", ":"),
        )
        return (
            str(self.runner_path.resolve()),
            "--mode=robot",
            f"--app-file={self.app_file.resolve()}",
            f"--app-params={parameters}",
        )

    def start_capture(self, request: CaptureRequest) -> CaptureHandle:
        """Start the exported app and retain its process for bounded waiting.

        Raises:
            FileNotFoundError: If the runner, exported app, or input CSV is missing.
        """

        for path, label in (
            (self.runner_path, "Yingdao runner"),
            (self.app_file, "Yingdao app file"),
            (request.input_csv, "Yingdao input CSV"),
        ):
            if not path.is_file():
                raise FileNotFoundError(f"{label} does not exist: {path}")
        request.raw_output_csv.parent.mkdir(parents=True, exist_ok=True)
        process = subprocess.Popen(
            self.build_command(request),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
        handle = CaptureHandle(run_id=f"local-{process.pid}")
        self._processes[handle.run_id] = process
        return handle

    def wait_for_capture(
        self,
        handle: CaptureHandle,
        request: CaptureRequest,
    ) -> CaptureResult:
        """Wait for process completion and require the promised raw CSV."""

        process = self._processes.pop(handle.run_id, None)
        if process is None:
            return CaptureResult(
                run_id=handle.run_id,
                status="failed",
                error_code="yingdao_run_not_found",
            )
        try:
            process.communicate(timeout=self.timeout_seconds)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
            return CaptureResult(
                run_id=handle.run_id,
                status="failed",
                error_code="yingdao_timeout",
            )
        if process.returncode != 0:
            return CaptureResult(
                run_id=handle.run_id,
                status="failed",
                error_code="yingdao_process_failed",
            )
        if not request.raw_output_csv.is_file():
            return CaptureResult(
                run_id=handle.run_id,
                status="failed",
                error_code="yingdao_output_missing",
            )
        return CaptureResult(run_id=handle.run_id, status="success")


@dataclass(slots=True)
class YingdaoApiRunner:
    """Run an enterprise Yingdao application through the official OpenAPI."""

    access_key_id: str
    access_key_secret: str
    account_name: str
    robot_uuid: str
    timeout_seconds: float = 600.0
    poll_interval_seconds: float = 3.0
    api_base_url: str = "https://api.yingdao.com"
    _access_token: str = ""

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, object] | None = None,
        form: dict[str, str] | None = None,
        query: dict[str, str] | None = None,
        authorized: bool = False,
    ) -> dict[str, object]:
        """Send one bounded JSON request without reflecting secret values."""

        headers = {"Accept": "application/json"}
        data: bytes | None = None
        if form is not None:
            data = urllib.parse.urlencode(form).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        elif payload is not None:
            data = json.dumps(payload, ensure_ascii=True).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if authorized:
            headers["Authorization"] = f"Bearer {self._access_token}"
        request_url = self.api_base_url.rstrip("/") + path
        if query:
            request_url += "?" + urllib.parse.urlencode(query)
        request = urllib.request.Request(
            request_url,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"yingdao_api_request_failed: {path}") from exc
        if not isinstance(result, dict):
            raise RuntimeError(f"yingdao_api_invalid_response: {path}")
        if result.get("success") is False or result.get("code") not in {0, 200, None}:
            raise RuntimeError(f"yingdao_api_rejected: {path}")
        return result

    @staticmethod
    def _data_object(response: dict[str, object]) -> dict[str, object]:
        """Return a response data mapping or an empty mapping."""

        data = response.get("data")
        return data if isinstance(data, dict) else {}

    def _authenticate(self) -> None:
        """Fetch a short-lived token using form data instead of URL parameters."""

        response = self._request_json(
            "POST",
            "/oapi/token/v2/token/create",
            form={
                "accessKeyId": self.access_key_id,
                "accessKeySecret": self.access_key_secret,
            },
        )
        token = self._data_object(response).get("accessToken")
        if not isinstance(token, str) or not token:
            raise RuntimeError("yingdao_api_token_missing")
        self._access_token = token

    def start_capture(self, request: CaptureRequest) -> CaptureHandle:
        """Authenticate and start one app job with string flow parameters."""

        if not request.input_csv.is_file():
            raise FileNotFoundError(f"Yingdao input CSV does not exist: {request.input_csv}")
        request.raw_output_csv.parent.mkdir(parents=True, exist_ok=True)
        self._authenticate()
        params = [
            {"name": name, "value": value, "type": "str"}
            for name, value in request.app_parameters().items()
        ]
        response = self._request_json(
            "POST",
            "/oapi/dispatch/v2/job/start",
            payload={
                "accountName": self.account_name,
                "robotUuid": self.robot_uuid,
                "params": params,
                "waitTimeout": f"{max(1, int(self.timeout_seconds // 60))}m",
            },
            authorized=True,
        )
        run_id = self._data_object(response).get("jobUuid")
        if not isinstance(run_id, str) or not run_id:
            raise RuntimeError("yingdao_job_uuid_missing")
        return CaptureHandle(run_id=run_id)

    def wait_for_capture(
        self,
        handle: CaptureHandle,
        request: CaptureRequest,
    ) -> CaptureResult:
        """Poll the job result until terminal and require its output CSV."""

        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            response = self._request_json(
                "GET",
                "/oapi/dispatch/v2/job/query",
                query={"jobUuid": handle.run_id},
                authorized=True,
            )
            status = str(self._data_object(response).get("status", "")).lower()
            if status in _SUCCESS_STATUSES:
                if request.raw_output_csv.is_file():
                    return CaptureResult(run_id=handle.run_id, status="success")
                return CaptureResult(
                    run_id=handle.run_id,
                    status="failed",
                    error_code="yingdao_output_missing",
                )
            if status in _FAILURE_STATUSES:
                return CaptureResult(
                    run_id=handle.run_id,
                    status="failed",
                    error_code="yingdao_job_failed",
                )
            time.sleep(self.poll_interval_seconds)
        return CaptureResult(
            run_id=handle.run_id,
            status="failed",
            error_code="yingdao_timeout",
        )


__all__ = [
    "CaptureHandle",
    "CaptureRequest",
    "CaptureResult",
    "YingdaoApiRunner",
    "YingdaoCommandRunner",
    "YingdaoRunner",
]
