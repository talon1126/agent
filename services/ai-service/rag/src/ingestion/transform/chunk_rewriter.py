"""Rewrite rough chunks through an injected LLM and versioned Prompt.

The transform improves semantic completeness while preserving source offsets,
source references, and image linkage. Rewritten text receives a new stable
chunk ID because content is part of the storage identity contract.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from hashlib import sha256
from typing import Any

from src.core.config import PromptTemplate
from src.core.errors import IngestionError
from src.core.types import Chunk
from src.ingestion.chunk.chunk_id import build_chunk_id
from src.libs.llm import BaseLLM, ChatMessage
from src.libs.transform.base_transform import BaseTransform

_IMAGE_PLACEHOLDER = re.compile(r"\[\[image:[^\]]+\]\]")


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
            Rewritten chunks with regenerated IDs. Execution provenance is
            reported by the Transform Pipeline sub-stage, not chunk metadata.

        Raises:
            IngestionError: If the LLM request fails or returns unusable text.
        """

        document_summary = _document_summary_from_context(context)
        return [
            self._rewrite_chunk(chunk, document_summary=document_summary)
            for chunk in chunks
        ]

    def _rewrite_chunk(self, chunk: Chunk, *, document_summary: str) -> Chunk:
        """Rewrite one chunk while keeping metadata free of provenance.

        Args:
            chunk: Source-addressable chunk to enhance.
            document_summary: Optional document-level semantic summary used to
                restore global context that may be absent from the chunk text.

        Returns:
            A rewritten chunk with regenerated ID, or a deep copy for chunks
            that only contain image placeholders.

        Raises:
            IngestionError: If prompt rendering, model execution, or response
                validation fails.
        """

        if _is_image_placeholder_only(chunk.text):
            return chunk.model_copy(deep=True)

        try:
            rewritten_text, _responses = self._rewrite_text_nodes(
                chunk.text,
                document_summary=document_summary,
            )
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
        return chunk.model_copy(
            update={
                "id": _chunk_id_for_text(chunk, rewritten_text),
                "text": rewritten_text,
                "metadata": metadata,
            },
            deep=True,
        )

    def _rewrite_text_nodes(
        self,
        text: str,
        *,
        document_summary: str,
    ) -> tuple[str, list[Any]]:
        """Rewrite text nodes while preserving image nodes in their exact order.

        Args:
            text: Source chunk text containing zero or more image placeholders.
            document_summary: Document-level semantic context.

        Returns:
            Reassembled searchable text and provider responses for provenance.
            Image placeholders and their surrounding whitespace never enter the
            LLM request, so the provider cannot delete, duplicate, or move them.

        Raises:
            ValueError: If a provider returns blank content for a text node.
        """

        nodes = re.split(f"({_IMAGE_PLACEHOLDER.pattern})", text)
        output: list[str] = []
        responses: list[Any] = []
        for node in nodes:
            if not node:
                continue
            if _IMAGE_PLACEHOLDER.fullmatch(node):
                output.append(node)
                continue
            leading = node[: len(node) - len(node.lstrip())]
            trailing = node[len(node.rstrip()) :]
            source_text = node.strip()
            if not source_text:
                output.append(node)
                continue
            response = self._llm.chat(
                [
                    ChatMessage(role="system", content=self._prompt.system_prompt),
                    ChatMessage(
                        role="user",
                        content=self._prompt.user_prompt.format(
                            chunk_text=source_text,
                            document_summary=document_summary,
                        ),
                    ),
                ]
            )
            rewritten = _extract_rewritten_text(response.content)
            if not rewritten:
                raise ValueError("Rewrite provider returned blank content")
            output.append(f"{leading}{rewritten}{trailing}")
            responses.append(response)
        if not responses:
            raise ValueError("Rewrite provider received no searchable text nodes")
        return "".join(output), responses


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
        or chunk.metadata.get("document_id")
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


def _is_image_placeholder_only(text: str) -> bool:
    """Return whether a chunk contains image references without text content.

    Args:
        text: Source chunk text produced by ``DocumentChunker``.

    Returns:
        ``True`` when removing every ``[[image:...]]`` placeholder and
        whitespace leaves no textual content.
    """

    return not _IMAGE_PLACEHOLDER.sub("", text).strip()


def _extract_rewritten_text(content: str) -> str:
    """Extract searchable chunk text from structured or markdown LLM replies.

    Args:
        content: Raw provider response. Providers may obey the JSON schema or
            return a markdown explanation that includes preserved metadata.

    Returns:
        Clean chunk text without metadata or image reference report sections.
    """

    stripped = content.strip()
    parsed_json, parsed_text = _text_from_json_payload(stripped)
    if parsed_json:
        return parsed_text
    return _strip_non_content_sections(stripped)


def _text_from_json_payload(content: str) -> tuple[bool, str]:
    """Parse a JSON rewrite payload without falling back on invalid text.

    Args:
        content: Raw provider response that may contain plain or fenced JSON.

    Returns:
        A pair of ``(parsed_json, text)``. ``parsed_json`` remains ``True`` when
        a valid JSON value lacks a non-empty text field so callers can reject
        the provider response instead of storing the raw JSON structure.
    """

    candidates = [content]
    if content.startswith("```"):
        lines = content.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            candidates.append("\n".join(lines[1:-1]))
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            return True, ""
        for key in ("text", "chunk", "rewritten_chunk", "rewritten_text"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return True, value.strip()
        return True, ""
    return False, ""


def _strip_non_content_sections(content: str) -> str:
    """Remove common metadata/report sections from markdown rewrite replies."""

    lines = content.splitlines()
    kept: list[str] = []
    for line in lines:
        normalized = line.strip().strip("*#:- ").lower()
        if normalized in {
            "metadata",
            "preserved metadata",
            "image references",
            "image refs",
        }:
            break
        kept.append(line)
    text = "\n".join(kept).strip()
    for prefix in (
        "### Rewritten Chunk",
        "## Rewritten Chunk",
        "# Rewritten Chunk",
        "**Rewritten chunk:**",
        "Rewritten chunk:",
        "Rewritten Chunk:",
    ):
        if text.startswith(prefix):
            text = text[len(prefix) :].strip()
            break
    return text


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
