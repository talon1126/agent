"""Wrap LangChain's RecursiveCharacterTextSplitter behind the local interface.

Only ``langchain-text-splitters`` is used. No LangChain RAG chains, retrievers,
memory, or document abstractions cross into this project layer.
"""

from __future__ import annotations

from typing import Any

from src.core.errors import IngestionError
from src.libs.splitter.base_splitter import BaseSplitter


class RecursiveCharacterSplitter(BaseSplitter):
    """Split text with LangChain's recursive character splitter utility."""

    def __init__(self, **options: Any) -> None:
        """Create the wrapped splitter from configuration options.

        Args:
            **options: Keyword arguments accepted by
                ``RecursiveCharacterTextSplitter``, usually ``chunk_size`` and
                ``chunk_overlap`` from ``settings.yaml``.

        Raises:
            IngestionError: If the installed splitter rejects the supplied
                options. When ``langchain-text-splitters`` is unavailable in a
                minimal test environment, the class falls back to a local
                character-window implementation so unit tests and offline
                development remain possible.
        """

        self._chunk_size = int(options.get("chunk_size", 1000))
        self._chunk_overlap = int(options.get("chunk_overlap", 0))
        invalid_window = (
            self._chunk_size <= 0
            or self._chunk_overlap < 0
            or self._chunk_overlap >= self._chunk_size
        )
        if invalid_window:
            raise IngestionError(
                "Invalid recursive splitter window options",
                context={
                    "operation": "splitter_init",
                    "chunk_size": self._chunk_size,
                    "chunk_overlap": self._chunk_overlap,
                },
            )
        try:
            from langchain_text_splitters import RecursiveCharacterTextSplitter

            self._splitter = RecursiveCharacterTextSplitter(**options)
            self._backend = "langchain"
        except ModuleNotFoundError:
            self._splitter = None
            self._backend = "local_fallback"
        except Exception as error:
            raise IngestionError(
                "Unable to initialize recursive character splitter",
                context={"operation": "splitter_init", "options": dict(options)},
                cause=error,
            ) from error

    def split(self, text: str) -> list[str]:
        """Split input text and return plain strings only.

        Args:
            text: Source text produced by a loader.

        Returns:
            Ordered non-blank text segments.

        Raises:
            IngestionError: If LangChain's splitter fails while processing text.
        """

        if self._splitter is None:
            return self._split_with_local_fallback(text)
        try:
            return [part for part in self._splitter.split_text(text) if part.strip()]
        except Exception as error:
            raise IngestionError(
                "Unable to split text with recursive character splitter",
                context={"operation": "splitter_split", "text_length": len(text)},
                cause=error,
            ) from error

    def _split_with_local_fallback(self, text: str) -> list[str]:
        """Split text with a deterministic overlap window when LangChain is absent.

        Args:
            text: Source text produced by a loader.

        Returns:
            Ordered non-blank text windows using the configured size and overlap.

        Notes:
            This fallback is intentionally basic and exists only to preserve
            offline tests and local development when the optional dependency has
            not been installed. Production environments should install
            ``langchain-text-splitters`` and use the LangChain backend.
        """

        chunks: list[str] = []
        start = 0
        while start < len(text):
            end = min(start + self._chunk_size, len(text))
            chunk = text[start:end]
            if chunk.strip():
                chunks.append(chunk)
            if end == len(text):
                break
            start = end - self._chunk_overlap
        return chunks
