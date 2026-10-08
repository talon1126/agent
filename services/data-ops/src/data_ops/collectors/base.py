"""Define the shared URL-CSV to raw-CSV collector boundary.

Collectors own page access and raw file delivery. They do not normalize rows,
write databases, or know how downstream manifests are produced. The contract
is intentionally small so Playwright and Yingdao can feed the same pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal, Protocol

CaptureStatus = Literal["success", "failed"]


class CollectorMode(StrEnum):
    """Select the concrete detail collector without changing CSV semantics."""

    PLAYWRIGHT = "playwright"
    YINGDAO = "yingdao"


@dataclass(frozen=True, slots=True)
class CaptureRequest:
    """Describe one bounded file handoff from URL discovery to a collector."""

    batch_id: str
    input_csv: Path
    raw_output_csv: Path

    def app_parameters(self) -> dict[str, str]:
        """Return the public application parameters accepted by Yingdao."""

        return {
            "batch_id": self.batch_id,
            "input_csv": str(self.input_csv.resolve()),
            "raw_output_csv": str(self.raw_output_csv.resolve()),
        }


@dataclass(frozen=True, slots=True)
class CaptureResult:
    """Report terminal collector delivery without exposing captured values."""

    run_id: str
    status: CaptureStatus
    error_code: str = ""
    captured_count: int = 0
    failed_count: int = 0


class ProductDetailCollector(Protocol):
    """Convert one canonical URL CSV into the shared raw product CSV."""

    def collect(self, request: CaptureRequest) -> CaptureResult:
        """Collect all input rows or return one stable batch failure."""


__all__ = [
    "CaptureRequest",
    "CaptureResult",
    "CaptureStatus",
    "CollectorMode",
    "ProductDetailCollector",
]
