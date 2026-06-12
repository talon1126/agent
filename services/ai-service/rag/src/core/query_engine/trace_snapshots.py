"""Build compact retrieval candidate snapshots for query trace stages.

Query traces must explain how candidates move through Dense, Sparse, Fusion,
Filter, and Rerank without storing full chunk text. The helpers in this module
therefore keep only stable identifiers, rank, score, and a small metadata
subset that is useful for later evaluation and database lookup.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from src.core.types import RetrievalResult

_TRACE_METADATA_KEYS = (
    "collection",
    "document_id",
    "doc_type",
    "topic",
    "section_path",
)


def candidate_snapshots(results: Sequence[RetrievalResult]) -> list[dict[str, Any]]:
    """Return ordered trace-safe snapshots for retrieval candidates.

    Args:
        results: Ranked retrieval candidates from one query stage.

    Returns:
        One dictionary per candidate containing one-based rank, chunk ID,
        numeric score, and selected metadata fields. Full text is intentionally
        omitted because callers can retrieve it by ``chunk_id`` when evaluating.
    """

    return [
        {
            "rank": index + 1,
            "chunk_id": result.chunk_id,
            "score": result.score,
            "metadata": _metadata_snapshot(result.metadata),
        }
        for index, result in enumerate(results)
    ]


def rejected_candidate_snapshots(
    rejected_chunk_ids: Mapping[str, Sequence[str]],
) -> list[dict[str, str]]:
    """Flatten filter rejection reasons into a deterministic trace list.

    Args:
        rejected_chunk_ids: Mapping from rejection reason to ordered chunk IDs.

    Returns:
        Dictionaries containing ``reason`` and ``chunk_id``. The structure is
        intentionally compact because full candidate metadata is available in
        the filter stage's ``before_candidates`` snapshot.
    """

    rejected: list[dict[str, str]] = []
    for reason in sorted(rejected_chunk_ids):
        for chunk_id in rejected_chunk_ids[reason]:
            rejected.append({"reason": reason, "chunk_id": chunk_id})
    return rejected


def _metadata_snapshot(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Copy only stable, evaluation-relevant metadata keys into trace output."""

    snapshot: dict[str, Any] = {}
    for key in _TRACE_METADATA_KEYS:
        if key in metadata and metadata[key] is not None:
            snapshot[key] = deepcopy(metadata[key])
    return snapshot
