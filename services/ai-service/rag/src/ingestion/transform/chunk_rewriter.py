"""Rewrite rough chunks through an injected LLM and versioned Prompt.

The transform improves semantic completeness while preserving source offsets,
source references, and image linkage. Rewritten text receives a new stable
chunk ID because content is part of the storage identity contract.
"""

from __future__ import annotations

import json
from copy import deepcopy
from hashlib import sha256
from typing import Any

from src.core.config import PromptTemplate
from src.core.errors import IngestionError
from src.core.types import Chunk
from src.ingestion.chunk.chunk_id import build_chunk_id
from src.libs.llm import BaseLLM, ChatMessage
from src.libs.transform.base_transform import BaseTransform


class ChunkRewriter(BaseTransform):
    """Use an LLM to rewrite chunks without changing source coordinates."""

    def __init__(self, *, llm: BaseLLM, prompt: PromptTemplate) -> None:
        """Configure the model and immutable Prompt contract.

        Args:
            llm: Provider-independent chat client selected by ``LLMFactory``.
            prompt: Validated rewrite Prompt loaded from configuration.
        """

        self._llm = llm
        self._prompt = prompt
        self._version = f"{prompt.name}:{prompt.version}"

    def transform(
        self,
        chunks: list[Chunk],
        *,
        context: dict[str, Any] | None = None,
    ) -> list[Chunk]:
        """Rewrite every not-yet-processed chunk and return independent copies.

        Args:
            chunks: Ordered source-addressable chunks.
            context: Optional runtime context reserved for trace orchestration.

        Returns:
            Rewritten chunks with regenerated IDs and rewrite provenance.
            Chunks already produced by the same Prompt version are copied
            without another model request.

        Raises:
            IngestionError: If the LLM request fails or returns unusable text.
        """

        document_summary = _document_summary_from_context(context)
        return [
            self._rewrite_chunk(chunk, document_summary=document_summary)
            for chunk in chunks
        ]

    def _rewrite_chunk(self, chunk: Chunk, *, document_summary: str) -> Chunk:
        """Rewrite one chunk unless its provenance proves idempotent output.

        Args:
            chunk: Source-addressable chunk to enhance.
            document_summary: Optional document-level semantic summary used to
                restore global context that may be absent from the chunk text.

        Returns:
            A rewritten chunk with regenerated ID and provider provenance, or a
            deep copy when the current text already matches this Prompt version.

        Raises:
            IngestionError: If prompt rendering, model execution, or response
                validation fails.
        """

        current_hash = _content_hash(chunk.text)
        rewrite_metadata = chunk.metadata.get("rewrite")
        if (
            isinstance(rewrite_metadata, dict)
            and rewrite_metadata.get("version") == self._version
            and rewrite_metadata.get("output_hash") == current_hash
        ):
            return chunk.model_copy(deep=True)

        try:
            response = self._llm.chat(
                [
                    ChatMessage(
                        role="system",
                        content=self._prompt.system_prompt,
                    ),
                    ChatMessage(
                        role="user",
                        content=self._prompt.user_prompt.format(
                            chunk_text=chunk.text,
                            document_summary=document_summary,
                            metadata=json.dumps(
                                chunk.metadata,
                                ensure_ascii=False,
                                sort_keys=True,
                            ),
                            image_refs=json.dumps(
                                chunk.metadata.get("image_refs", []),
                                ensure_ascii=False,
                            ),
                        ),
                    ),
                ]
            )
            rewritten_text = response.content.strip()
            if not rewritten_text:
                raise ValueError("Rewrite provider returned blank content")
        except Exception as error:
            raise IngestionError(
                "Unable to rewrite chunk",
                context={
                    "operation": "rewrite_chunk",
                    "chunk_id": chunk.id,
                    "prompt_version": self._version,
                },
                cause=error,
            ) from error

        metadata = deepcopy(chunk.metadata)
        metadata["rewrite"] = {
            "version": self._version,
            "provider": response.provider,
            "model": response.model,
            "input_hash": current_hash,
            "output_hash": _content_hash(rewritten_text),
        }
        return chunk.model_copy(
            update={
                "id": _chunk_id_for_text(chunk, rewritten_text),
                "text": rewritten_text,
                "metadata": metadata,
            },
            deep=True,
        )


def _chunk_id_for_text(chunk: Chunk, text: str) -> str:
    """Rebuild one chunk ID after a text-changing transform.

    Args:
        chunk: Source chunk providing path and section identity.
        text: New searchable text produced by the model.

    Returns:
        Stable content-addressed chunk ID.
    """

    source_path = str(
        chunk.metadata.get("source_path")
        or (chunk.source_ref or {}).get("source_path")
        or (chunk.source_ref or {}).get("document_id")
        or chunk.id
    )
    return build_chunk_id(
        source_path=source_path,
        section_path=chunk.metadata.get("section_path"),
        text=text,
    )


def _content_hash(text: str) -> str:
    """Return the content digest used by transform idempotency metadata.

    Args:
        text: Exact source or rewritten text.

    Returns:
        Lowercase SHA256 hexadecimal digest.
    """

    return sha256(text.encode("utf-8")).hexdigest()


def _document_summary_from_context(context: dict[str, Any] | None) -> str:
    """Extract a safe document summary string from transform runtime context.

    Args:
        context: Optional context supplied by ``IngestionPipeline``.

    Returns:
        A stripped summary string, or an empty string when summary generation is
        disabled or unavailable.
    """

    if not isinstance(context, dict):
        return ""
    value = context.get("document_summary")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""
