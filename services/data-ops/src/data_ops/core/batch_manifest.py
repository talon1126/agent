"""Persist generic file-batch identity, lifecycle state, and replay metadata.

The manifest records hashes, contract identity, row counts, status, and
artifact filenames. It never stores source rows or credentials. Archive and
quarantine transitions stage outputs and the manifest before moving the
immutable source file, then publish the complete batch with one directory
rename.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

from data_ops.core.contracts import DatasetContract, ProcessorResult

BatchStatus = Literal["success", "failed"]
_SAFE_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_SENSITIVE_VALUE_PATTERN = re.compile(
    r"(?i)\b(password|cookie|token|authorization|secret)\b\s*[:=]\s*([^\s,;]+)"
)


class BatchManifestError(ValueError):
    """Raised when manifest data or a filesystem transition is unsafe."""


class DuplicateBatchError(BatchManifestError):
    """Raised when source bytes and contract identity were already processed."""


@dataclass(frozen=True, slots=True)
class BatchManifest:
    """Represent the versioned, site-independent batch manifest schema."""

    batch_id: str
    dataset_type: str
    status: BatchStatus
    input_sha256: str
    contract_sha256: str
    deduplication_key: str
    processor: str
    row_counts: Mapping[str, int]
    input_file: str
    output_files: tuple[str, ...]
    error_code: str = ""
    error_summary: str = ""
    replayable: bool = True
    schema_version: int = 1

    def __post_init__(self) -> None:
        """Freeze mappings and reject paths, counts, or statuses that are unsafe."""

        if type(self.schema_version) is not int or self.schema_version != 1:
            raise BatchManifestError("unsupported schema_version")
        if type(self.replayable) is not bool:
            raise BatchManifestError("replayable must be a JSON boolean")
        _validate_segment(self.batch_id, label="batch_id")
        _validate_segment(self.dataset_type, label="dataset_type")
        if self.status not in {"success", "failed"}:
            raise BatchManifestError(f"unsupported batch status: {self.status}")
        if not re.fullmatch(r"[0-9a-f]{64}", self.input_sha256):
            raise BatchManifestError("input_sha256 must be a lowercase SHA-256")
        if self.contract_sha256 and not re.fullmatch(
            r"[0-9a-f]{64}",
            self.contract_sha256,
        ):
            raise BatchManifestError("contract_sha256 must be empty or a SHA-256")
        if not re.fullmatch(r"[0-9a-f]{64}", self.deduplication_key):
            raise BatchManifestError("deduplication_key must be a lowercase SHA-256")
        if not self.processor.strip():
            raise BatchManifestError("processor must not be blank")
        _validate_filename(self.input_file, label="input_file")
        for filename in self.output_files:
            _validate_filename(filename, label="output_file")
        if len(self.output_files) != len(set(self.output_files)):
            raise BatchManifestError("output_files must not contain duplicates")
        if self.input_file in self.output_files or "manifest.json" in self.output_files:
            raise BatchManifestError("artifact filenames must not collide")

        frozen_counts = dict(self.row_counts)
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in frozen_counts.values()
        ):
            raise BatchManifestError("row_counts must contain non-negative integers")
        if self.status == "success" and (self.error_code or self.error_summary):
            raise BatchManifestError("successful manifests cannot contain errors")
        if self.status == "failed" and not self.error_code:
            raise BatchManifestError("failed manifests require error_code")
        if self.replayable and not self.contract_sha256:
            raise BatchManifestError("replayable manifests require contract identity")
        object.__setattr__(self, "row_counts", MappingProxyType(frozen_counts))

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible mapping with a stable key contract."""

        return {
            "schema_version": self.schema_version,
            "batch_id": self.batch_id,
            "dataset_type": self.dataset_type,
            "status": self.status,
            "input_sha256": self.input_sha256,
            "contract_sha256": self.contract_sha256,
            "deduplication_key": self.deduplication_key,
            "processor": self.processor,
            "row_counts": dict(self.row_counts),
            "input_file": self.input_file,
            "output_files": list(self.output_files),
            "error_code": self.error_code,
            "error_summary": self.error_summary,
            "replayable": self.replayable,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> BatchManifest:
        """Validate and construct a manifest loaded from JSON."""

        try:
            return cls(
                schema_version=payload["schema_version"],
                batch_id=str(payload["batch_id"]),
                dataset_type=str(payload["dataset_type"]),
                status=str(payload["status"]),
                input_sha256=str(payload["input_sha256"]),
                contract_sha256=str(payload["contract_sha256"]),
                deduplication_key=str(payload["deduplication_key"]),
                processor=str(payload["processor"]),
                row_counts=dict(payload["row_counts"]),
                input_file=str(payload["input_file"]),
                output_files=tuple(str(value) for value in payload["output_files"]),
                error_code=str(payload.get("error_code", "")),
                error_summary=str(payload.get("error_summary", "")),
                replayable=payload.get("replayable", False),
            )
        except (KeyError, TypeError, ValueError) as exc:
            if isinstance(exc, BatchManifestError):
                raise
            raise BatchManifestError(f"invalid manifest payload: {exc}") from exc


def _validate_segment(value: str, *, label: str) -> None:
    """Reject path traversal and separators in dataset and batch identifiers."""

    if _SAFE_SEGMENT_PATTERN.fullmatch(value) is None:
        raise BatchManifestError(f"{label} contains unsafe path characters")


def _validate_filename(value: str, *, label: str) -> None:
    """Require a plain filename rather than a path."""

    if not value or Path(value).name != value or value in {".", ".."}:
        raise BatchManifestError(f"{label} must be a plain filename")


def _sanitize_error_summary(summary: str) -> str:
    """Redact credential-like assignments and collapse unbounded text."""

    collapsed = " ".join(str(summary).split())
    redacted = _SENSITIVE_VALUE_PATTERN.sub(
        lambda match: f"{match.group(1)}=[REDACTED]",
        collapsed,
    )
    return redacted[:500]


def calculate_file_sha256(path: str | Path) -> str:
    """Calculate the SHA-256 digest of immutable source-file bytes.

    Args:
        path: Existing regular file.

    Returns:
        Lowercase hexadecimal SHA-256 digest.

    Raises:
        BatchManifestError: If path is not a regular file.
    """

    source = Path(path)
    if not source.is_file():
        raise BatchManifestError(f"cannot hash missing file: {source}")
    digest = hashlib.sha256()
    with source.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def calculate_contract_sha256(contract: DatasetContract) -> str:
    """Calculate a deterministic identity for processor-facing contract fields."""

    payload = {
        "dataset_type": contract.dataset_type,
        "required_columns": list(contract.required_columns),
        "optional_columns": list(contract.optional_columns),
        "column_types": [
            [column, contract.column_types[column]] for column in contract.all_columns
        ],
        "unique_by": list(contract.unique_by),
        "normalized_filename_template": contract.normalized_filename_template,
        "failed_filename_template": contract.failed_filename_template,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def build_batch_manifest(
    *,
    batch_id: str,
    dataset_type: str,
    input_path: str | Path,
    contract: DatasetContract | None,
    processor: str,
    row_counts: Mapping[str, int],
    status: BatchStatus,
    output_files: Sequence[str | Path] = (),
    error_code: str = "",
    error_summary: str = "",
) -> BatchManifest:
    """Build one deterministic manifest without reading source row contents.

    Args:
        batch_id: Stable batch identifier.
        dataset_type: Processor routing key.
        input_path: Existing immutable source file.
        contract: Active contract, or None for an unregistered non-replayable
            failure.
        processor: Qualified processor implementation name.
        row_counts: Integer reconciliation counts.
        status: Success or failed lifecycle state.
        output_files: Output artifacts identified by basename only.
        error_code: Stable failure code.
        error_summary: Short operator-facing summary subject to redaction.

    Returns:
        Validated immutable manifest.
    """

    source = Path(input_path)
    input_sha256 = calculate_file_sha256(source)
    contract_sha256 = calculate_contract_sha256(contract) if contract else ""
    identity = json.dumps(
        {"input_sha256": input_sha256, "contract_sha256": contract_sha256},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    suffix = source.suffix.lower()
    input_file = f"source{suffix if suffix else '.bin'}"
    output_names = tuple(Path(path).name for path in output_files)
    if contract is not None and contract.dataset_type != dataset_type:
        raise BatchManifestError("manifest dataset_type does not match contract")
    return BatchManifest(
        batch_id=batch_id,
        dataset_type=dataset_type,
        status=status,
        input_sha256=input_sha256,
        contract_sha256=contract_sha256,
        deduplication_key=hashlib.sha256(identity).hexdigest(),
        processor=processor,
        row_counts=row_counts,
        input_file=input_file,
        output_files=output_names,
        error_code=error_code,
        error_summary=_sanitize_error_summary(error_summary),
        replayable=contract is not None,
    )


def _write_manifest_atomic(manifest: BatchManifest, destination: Path) -> None:
    """Write deterministic JSON to a temporary sibling and atomically replace."""

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".manifest-",
        suffix=".tmp",
        dir=destination.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        temporary_path.write_text(
            json.dumps(
                manifest.to_dict(),
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary_path, destination)
    finally:
        temporary_path.unlink(missing_ok=True)


def load_batch_manifest(path: str | Path) -> BatchManifest:
    """Load and validate one manifest JSON file."""

    manifest_path = Path(path)
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BatchManifestError(f"cannot load manifest: {manifest_path}") from exc
    if not isinstance(payload, dict):
        raise BatchManifestError("manifest root must be a JSON object")
    return BatchManifest.from_dict(payload)


def find_duplicate_batch(
    root: str | Path,
    deduplication_key: str,
) -> Path | None:
    """Find a published manifest with the same source and contract identity."""

    search_root = Path(root)
    if not search_root.exists():
        return None
    for manifest_path in sorted(search_root.rglob("manifest.json")):
        try:
            manifest = load_batch_manifest(manifest_path)
        except BatchManifestError:
            continue
        if manifest.deduplication_key == deduplication_key:
            return manifest_path
    return None


def _commit_batch_directory(
    manifest: BatchManifest,
    *,
    source_path: str | Path,
    output_paths: Sequence[str | Path],
    root: str | Path,
    required_status: BatchStatus,
) -> Path:
    """Stage artifacts, move source last, and publish one complete directory."""

    if manifest.status != required_status:
        raise BatchManifestError(
            f"{required_status} transition requires matching manifest status"
        )
    source = Path(source_path)
    if not source.is_file():
        raise BatchManifestError(f"source file does not exist: {source}")
    outputs = tuple(Path(path) for path in output_paths)
    if tuple(path.name for path in outputs) != manifest.output_files:
        raise BatchManifestError("output_paths do not match manifest output_files")
    if any(not path.is_file() for path in outputs):
        raise BatchManifestError("all manifest outputs must exist before transition")

    lifecycle_root = Path(root)
    duplicate = find_duplicate_batch(lifecycle_root, manifest.deduplication_key)
    if duplicate is not None:
        raise DuplicateBatchError(f"duplicate batch already exists: {duplicate}")
    dataset_root = lifecycle_root / manifest.dataset_type
    dataset_root.mkdir(parents=True, exist_ok=True)
    target = dataset_root / manifest.batch_id
    if target.exists():
        raise BatchManifestError(f"batch target already exists: {target}")

    staging = Path(
        tempfile.mkdtemp(
            prefix=f".{manifest.batch_id}-",
            dir=dataset_root,
        )
    )
    staged_source = staging / manifest.input_file
    source_moved = False
    try:
        for output in outputs:
            shutil.copy2(output, staging / output.name)
        _write_manifest_atomic(manifest, staging / "manifest.json")
        shutil.move(str(source), staged_source)
        source_moved = True
        os.replace(staging, target)
    except Exception:
        if source_moved and staged_source.exists() and not source.exists():
            shutil.move(str(staged_source), source)
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return target


def archive_successful_batch(
    manifest: BatchManifest,
    *,
    source_path: str | Path,
    output_paths: Sequence[str | Path],
    archive_root: str | Path,
) -> Path:
    """Publish a successful source, output set, and manifest under archive."""

    return _commit_batch_directory(
        manifest,
        source_path=source_path,
        output_paths=output_paths,
        root=archive_root,
        required_status="success",
    )


def quarantine_failed_batch(
    manifest: BatchManifest,
    *,
    source_path: str | Path,
    failed_root: str | Path,
    output_paths: Sequence[str | Path] = (),
) -> Path:
    """Publish a failed source, optional outputs, and replay manifest."""

    return _commit_batch_directory(
        manifest,
        source_path=source_path,
        output_paths=output_paths,
        root=failed_root,
        required_status="failed",
    )


def replay_batch(
    manifest_path: str | Path,
    *,
    output_root: str | Path,
    source_path: str | Path | None = None,
) -> ProcessorResult[Any]:
    """Replay archived or corrected source through the registered processor.

    Args:
        manifest_path: Published manifest in archive or failed storage.
        output_root: Directory that receives replayed normalized and failed CSV.
        source_path: Optional corrected source copy. When omitted, replay uses
            the immutable source stored beside the manifest.

    Returns:
        The generic processor result.

    Raises:
        BatchManifestError: If replay is disabled, source is missing, its batch
            identifier differs from the manifest, or the registered contract
            differs from the archived identity.
    """

    manifest_file = Path(manifest_path)
    manifest = load_batch_manifest(manifest_file)
    if not manifest.replayable:
        raise BatchManifestError("manifest is not replayable")
    source = (
        Path(source_path)
        if source_path is not None
        else manifest_file.parent / manifest.input_file
    )
    if not source.is_file():
        raise BatchManifestError("manifest replay source is missing")

    from data_ops.cli import process_dataset
    from data_ops.core.csv_io import read_source_file
    from data_ops.processors.registry import get_processor

    processor = get_processor(manifest.dataset_type)
    if calculate_contract_sha256(processor.contract) != manifest.contract_sha256:
        raise BatchManifestError("manifest contract does not match current processor")
    frame = read_source_file(source)
    source_batch_ids = (
        {str(value).strip() for value in frame["batch_id"].tolist()}
        if "batch_id" in frame
        else set()
    )
    if source_batch_ids != {manifest.batch_id}:
        raise BatchManifestError("replay source batch_id does not match manifest")
    return process_dataset(
        manifest.dataset_type,
        source,
        output_root,
    )


__all__ = [
    "BatchManifest",
    "BatchManifestError",
    "DuplicateBatchError",
    "archive_successful_batch",
    "build_batch_manifest",
    "calculate_contract_sha256",
    "calculate_file_sha256",
    "find_duplicate_batch",
    "load_batch_manifest",
    "quarantine_failed_batch",
    "replay_batch",
]
