"""Provide deterministic splitter behavior for unit tests."""

from __future__ import annotations

from src.libs.splitter.base_splitter import BaseSplitter


class FakeSplitter(BaseSplitter):
    """Return configured text segments without inspecting input text."""

    def __init__(self, chunks: list[str] | tuple[str, ...] | None = None) -> None:
        """Configure deterministic split output.

        Args:
            chunks: Ordered text segments returned by ``split()``. When omitted,
                the fake returns the original input as a single segment.
        """

        self._chunks = list(chunks) if chunks is not None else None

    def split(self, text: str) -> list[str]:
        """Return configured chunks or the input text as one segment.

        Args:
            text: Source text supplied by the caller.

        Returns:
            A list of non-blank strings. Blank configured chunks are filtered so
            tests exercise the same contract expected from real splitters.
        """

        chunks = self._chunks if self._chunks is not None else [text]
        return [chunk for chunk in chunks if chunk.strip()]
