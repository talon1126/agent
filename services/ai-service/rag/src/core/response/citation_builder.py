"""Build source-grounded citations from ranked retrieval results.

``CitationBuilder`` is the attribution boundary between retrieval and response
assembly. It reads document identity, source location, title, and section
metadata already attached during ingestion or hydration. It never derives a
source from chunk prose and fails explicitly when a candidate cannot be tied to
a real document path.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse

from src.core.types import Citation, RetrievalResult


class CitationBuilder:
    """Convert ranked retrieval results into immutable citation objects."""

    def build(
        self,
        candidates: Sequence[RetrievalResult],
        *,
        trace_id: str,
    ) -> list[Citation]:
        """Build one grounded citation per ranked retrieval candidate.

        Args:
            candidates: Final ranked retrieval results in response order.
            trace_id: Non-blank query trace identifier shared by every citation.

        Returns:
            Citations in the same order as ``candidates``.

        Raises:
            ValueError: If ``trace_id`` is blank or a candidate lacks a stable
                document ID, source path, usable title, or valid section path.

        Notes:
            ``source_ref`` fields take precedence because they were created for
            citation fidelity during chunking. Top-level metadata remains
            supported for Dense results and older persisted chunks.
        """

        if not isinstance(trace_id, str) or not trace_id.strip():
            raise ValueError("trace_id must not be blank")

        return [
            self._build_one(candidate, trace_id=trace_id)
            for candidate in candidates
        ]

    def _build_one(
        self,
        candidate: RetrievalResult,
        *,
        trace_id: str,
    ) -> Citation:
        """Construct one citation from a retrieval result's source metadata.

        Args:
            candidate: Ranked retrieval result.
            trace_id: Validated query trace identifier.

        Returns:
            Immutable citation grounded in source metadata.

        Raises:
            ValueError: If required source metadata is missing or malformed.
        """

        metadata = dict(candidate.metadata)
        source_ref_value = metadata.get("source_ref", {})
        if source_ref_value is None:
            source_ref: Mapping[str, Any] = {}
        elif isinstance(source_ref_value, Mapping):
            source_ref = source_ref_value
        else:
            raise ValueError(
                f"Citation source_ref must be a mapping for chunk "
                f"'{candidate.chunk_id}'"
            )

        document_id = self._required_string(
            source_ref.get("document_id", metadata.get("document_id")),
            field_name="document_id",
            chunk_id=candidate.chunk_id,
        )
        source_uri = self._required_string(
            self._first_present(
                source_ref,
                metadata,
                keys=("source_uri", "source_path"),
            ),
            field_name="source path",
            chunk_id=candidate.chunk_id,
        )
        title_value = self._first_present(
            source_ref,
            metadata,
            keys=("title",),
        )
        title = (
            self._required_string(
                title_value,
                field_name="title",
                chunk_id=candidate.chunk_id,
            )
            if title_value is not None
            else self._title_from_source_uri(
                source_uri,
                chunk_id=candidate.chunk_id,
            )
        )
        section_value = self._first_present(
            source_ref,
            metadata,
            keys=("section_path", "heading_path"),
        )
        section_path = self._normalize_section_path(
            section_value,
            chunk_id=candidate.chunk_id,
        )

        return Citation(
            document_id=document_id,
            chunk_id=candidate.chunk_id,
            title=title,
            section_path=section_path,
            source_uri=source_uri,
            score=candidate.score,
            trace_id=trace_id,
        )

    @staticmethod
    def _first_present(
        primary: Mapping[str, Any],
        fallback: Mapping[str, Any],
        *,
        keys: tuple[str, ...],
    ) -> Any:
        """Return the first non-null value using source_ref precedence.

        Args:
            primary: Preferred source reference mapping.
            fallback: Top-level retrieval metadata.
            keys: Ordered aliases accepted for one citation field.

        Returns:
            First non-null value, or ``None`` when no mapping contains one.
        """

        for mapping in (primary, fallback):
            for key in keys:
                value = mapping.get(key)
                if value is not None:
                    return value
        return None

    @staticmethod
    def _required_string(
        value: Any,
        *,
        field_name: str,
        chunk_id: str,
    ) -> str:
        """Validate one required metadata value as a non-blank string.

        Args:
            value: Metadata value to validate.
            field_name: Human-readable field label for failures.
            chunk_id: Candidate identifier used for diagnostics.

        Returns:
            Original non-blank string.

        Raises:
            ValueError: If the value is not a string or is blank.
        """

        if not isinstance(value, str):
            raise ValueError(
                f"Citation {field_name} must be a string for chunk "
                f"'{chunk_id}'"
            )
        if not value.strip():
            raise ValueError(
                f"Citation {field_name} must not be blank for chunk '{chunk_id}'"
            )
        return value

    @staticmethod
    def _title_from_source_uri(source_uri: str, *, chunk_id: str) -> str:
        """Derive a display title only from the verified source location.

        Args:
            source_uri: Canonical path or external URI.
            chunk_id: Candidate identifier used for diagnostics.

        Returns:
            Filename stem such as ``wireless-earbuds``.

        Raises:
            ValueError: If the source has no usable filename component.

        Notes:
            This fallback remains source-grounded. It never inspects or
            summarizes chunk text to invent a document title.
        """

        parsed = urlparse(source_uri)
        path = parsed.path if parsed.scheme else source_uri
        filename = PurePosixPath(path.replace("\\", "/").rstrip("/")).name
        title = unquote(PurePosixPath(filename).stem)
        if not title.strip():
            raise ValueError(
                f"Citation title cannot be derived from source path for chunk "
                f"'{chunk_id}'"
            )
        return title

    @staticmethod
    def _normalize_section_path(
        value: Any,
        *,
        chunk_id: str,
    ) -> tuple[str, ...]:
        """Normalize optional section metadata to an immutable path.

        Args:
            value: String, ordered string sequence, or ``None``.
            chunk_id: Candidate identifier used for diagnostics.

        Returns:
            Ordered non-blank section components.

        Raises:
            ValueError: If the value is not a string/sequence or contains a
                non-string or blank component.
        """

        if value is None:
            return ()
        if isinstance(value, str):
            values = (value,)
        elif isinstance(value, Sequence) and not isinstance(
            value,
            (bytes, bytearray),
        ):
            values = tuple(value)
        else:
            raise ValueError(
                f"Citation section path must be a string or sequence for chunk "
                f"'{chunk_id}'"
            )

        normalized: list[str] = []
        for component in values:
            if not isinstance(component, str) or not component.strip():
                raise ValueError(
                    f"Citation section path contains an invalid component for "
                    f"chunk '{chunk_id}'"
                )
            normalized.append(component)
        return tuple(normalized)
