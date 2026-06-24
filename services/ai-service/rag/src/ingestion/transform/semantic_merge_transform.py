"""Merge logically continuous adjacent chunks through an injected LLM.

Only chunks in the same section are compared. The model returns a structured
decision, while this class owns deterministic metadata, source-range, image
reference, ordering, and stable-ID reconstruction.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from src.core.config import PromptTemplate
from src.core.errors import IngestionError
from src.core.types import Chunk
from src.ingestion.chunk.chunk_id import build_chunk_id
from src.libs.llm import BaseLLM, ChatMessage, LLMResponse
from src.libs.transform.base_transform import BaseTransform


class SemanticMergeTransform(BaseTransform):
    """Ask an LLM whether adjacent same-section chunks should be combined."""

    def __init__(self, *, llm: BaseLLM, prompt: PromptTemplate) -> None:
        """Configure the semantic decision model and versioned Prompt.

        Args:
            llm: Provider-independent chat client used only for adjacent chunks
                that share the same logical section.
            prompt: Validated structured-decision Prompt loaded from config.
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
        """Evaluate adjacent chunks once and return a reindexed output list.

        Args:
            chunks: Ordered chunks from the same document.
            context: Optional trace context reserved for orchestration.

        Returns:
            New chunks with approved adjacent pairs merged. Execution details
            belong to transform trace sub-stages, not chunk metadata.
        """

        del context
        output: list[Chunk] = []
        index = 0
        while index < len(chunks):
            left = chunks[index]
            if index + 1 >= len(chunks):
                output.append(self._mark_evaluated(left, status="single"))
                break

            right = chunks[index + 1]
            if _section_path(left) != _section_path(right):
                output.append(self._mark_evaluated(left, status="section_boundary"))
                index += 1
                continue

            decision, response = self._request_decision(left, right)
            if decision.get("merge") is True and str(decision.get("merged_text", "")).strip():
                output.append(
                    self._merge_pair(
                        left,
                        right,
                        merged_text=str(decision["merged_text"]).strip(),
                        provider=response.provider,
                        model=response.model,
                    )
                )
                index += 2
                continue

            output.append(
                self._mark_evaluated(
                    left,
                    status="kept_separate",
                    provider=response.provider,
                    model=response.model,
                )
            )
            index += 1

        reindexed: list[Chunk] = []
        for chunk_index, chunk in enumerate(output):
            metadata = deepcopy(chunk.metadata)
            metadata["chunk_index"] = chunk_index
            reindexed.append(
                chunk.model_copy(
                    update={
                        "chunk_index": chunk_index,
                        "metadata": metadata,
                    },
                    deep=True,
                )
            )
        return reindexed

    def _request_decision(
        self,
        left: Chunk,
        right: Chunk,
    ) -> tuple[dict[str, Any], LLMResponse]:
        """Request and parse one structured adjacent-chunk decision.

        Args:
            left: Earlier chunk in document order.
            right: Immediately following chunk from the same section.

        Returns:
            Parsed decision mapping and the normalized provider response used
            for provenance metadata. Invalid JSON becomes a conservative
            ``merge=false`` decision.

        Raises:
            IngestionError: If the model call fails before a response exists.
        """

        try:
            response = self._llm.chat(
                [
                    ChatMessage(role="system", content=self._prompt.system_prompt),
                    ChatMessage(
                        role="user",
                        content=self._prompt.user_prompt.format(
                            chunk_a_text=left.text,
                            chunk_b_text=right.text,
                            metadata=json.dumps(
                                {
                                    "section_path": _section_path(left),
                                    "source_path": left.metadata.get("source_path"),
                                },
                                ensure_ascii=False,
                                sort_keys=True,
                            ),
                        ),
                    ),
                ]
            )
        except Exception as error:
            raise IngestionError(
                "Unable to evaluate semantic merge",
                context={
                    "operation": "semantic_merge",
                    "left_chunk_id": left.id,
                    "right_chunk_id": right.id,
                    "prompt_version": self._version,
                },
                cause=error,
            ) from error
        try:
            decision = json.loads(response.content)
        except (TypeError, json.JSONDecodeError):
            decision = {"merge": False}
        if not isinstance(decision, dict):
            decision = {"merge": False}
        return decision, response

    def _merge_pair(
        self,
        left: Chunk,
        right: Chunk,
        *,
        merged_text: str,
        provider: str,
        model: str,
    ) -> Chunk:
        """Combine one approved pair while preserving source provenance.

        Args:
            left: Earlier source chunk.
            right: Later adjacent source chunk.
            merged_text: Model-produced text preserving both source chunks.
            provider: Provider registry key used for the decision.
            model: Model identifier used for the decision.

        Returns:
            One chunk spanning both source ranges with merged image references,
            regenerated content identity, and decision provenance.
        """

        metadata = deepcopy(left.metadata)
        metadata["image_refs"] = _ordered_unique(
            [
                *left.metadata.get("image_refs", []),
                *right.metadata.get("image_refs", []),
            ]
        )
        if not metadata["image_refs"]:
            metadata.pop("image_refs")
        del provider, model

        source_path = str(
            metadata.get("source_path")
            or metadata.get("document_id")
            or left.id
        )
        return left.model_copy(
            update={
                "id": build_chunk_id(
                    source_path=source_path,
                    section_path=_section_path(left),
                    text=merged_text,
                ),
                "text": merged_text,
                "metadata": metadata,
                "start_offset": min(left.start_offset, right.start_offset),
                "end_offset": max(left.end_offset, right.end_offset),
            },
            deep=True,
        )

    def _mark_evaluated(
        self,
        chunk: Chunk,
        *,
        status: str,
        provider: str | None = None,
        model: str | None = None,
    ) -> Chunk:
        """Return one unchanged chunk copy without provenance metadata.

        Args:
            chunk: Source chunk retained without text changes.
            status: Decision outcome such as ``single`` or ``kept_separate``.
            provider: Optional provider key when the model was consulted.
            model: Optional model identifier when the model was consulted.

        Returns:
            A deep copy preserving caller-visible metadata.
        """

        del status, provider, model
        return chunk.model_copy(deep=True)


def _section_path(chunk: Chunk) -> list[str]:
    """Normalize the chunk section path used for merge boundaries.

    Args:
        chunk: Chunk whose metadata may store a string or sequence path.

    Returns:
        Ordered string path components.
    """

    value = chunk.metadata.get("section_path", [])
    if isinstance(value, str):
        return [value] if value else []
    return [str(component) for component in value]


def _ordered_unique(values: list[Any]) -> list[str]:
    """Return unique string values while preserving source order.

    Args:
        values: Image IDs or other JSON-compatible identifiers.

    Returns:
        String-normalized values with duplicate occurrences removed.
    """

    return list(dict.fromkeys(str(value) for value in values))
