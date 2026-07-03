"""Build the public knowledge-hub response from ranked retrieval results.

``KnowledgeHubResponseBuilder`` is the final core-layer projection before MCP,
CLI, Dashboard, or AImodel adapters serialize data. It formats retrieved text
as numbered evidence, optionally optimizes that evidence into Agent-ready final
context, delegates grounded citations to ``CitationBuilder``, and delegates
optional image resolution to ``MultimodalAssembler``. The resulting model
intentionally contains no route diagnostics, vectors, provider payloads, or
internal tool JSON.

This module does not generate a final natural-language answer. The returned
content is context for an Agent or caller to use while preserving citations.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core.response.citation_builder import CitationBuilder
from src.core.response.multimodal_assembler import (
    MultimodalAssembler,
    ResponseImage,
)
from src.core.types import Citation, RetrievalResult


class EvidenceContextOptimizerLike(Protocol):
    """Describe the response-builder boundary for optional context optimization."""

    def optimize(self, *, query: str, evidence: str) -> str:
        """Return Agent-ready context for one user query and evidence block."""


class KnowledgeHubResponse(BaseModel):
    """Represent the stable public response returned by RAG query adapters.

    Attributes:
        ok: Successful query execution flag. Empty retrieval is still success.
        content: Ranked plain-text evidence formatted for Agent consumption.
        citations: Grounded source references aligned with ranked candidates.
        images: Resolved images referenced by the ranked candidates.
        trace_id: Query trace identifier used for observability and support.
        is_empty: Explicit marker distinguishing no hits from failed execution.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    ok: bool = True
    content: str
    citations: tuple[Citation, ...] = ()
    images: tuple[ResponseImage, ...] = ()
    trace_id: str = Field(min_length=1)
    is_empty: bool

    @field_validator("trace_id")
    @classmethod
    def reject_blank_trace_id(cls, value: str) -> str:
        """Require every public response to remain trace-correlated.

        Args:
            value: Candidate query trace identifier.

        Returns:
            Original non-blank trace ID.

        Raises:
            ValueError: If the trace ID contains only whitespace.
        """

        if not value.strip():
            raise ValueError("trace_id must not be blank")
        return value


class KnowledgeHubResponseBuilder:
    """Construct public text, citations, and image references."""

    def __init__(
        self,
        *,
        citation_builder: CitationBuilder | None = None,
        multimodal_assembler: MultimodalAssembler | None = None,
        evidence_context_optimizer: EvidenceContextOptimizerLike | None = None,
        fallback_to_raw_content: bool = True,
    ) -> None:
        """Configure replaceable response-layer collaborators.

        Args:
            citation_builder: Source attribution component. ``None`` uses the
                default grounded ``CitationBuilder``.
            multimodal_assembler: Optional image resolver. ``None`` creates a
                text-only assembler so RAG remains usable without image
                storage wiring.
            evidence_context_optimizer: Optional component that turns raw
                numbered evidence into Agent-ready final context.
            fallback_to_raw_content: Whether optimizer failures should degrade
                to raw evidence instead of failing the whole query.
        """

        self._citation_builder = citation_builder or CitationBuilder()
        self._multimodal_assembler = (
            multimodal_assembler or MultimodalAssembler()
        )
        self._evidence_context_optimizer = evidence_context_optimizer
        self._fallback_to_raw_content = fallback_to_raw_content

    def build(
        self,
        candidates: Sequence[RetrievalResult],
        *,
        trace_id: str,
        query: str | None = None,
    ) -> KnowledgeHubResponse:
        """Build one immutable public response from final ranked candidates.

        Args:
            candidates: Results after hybrid retrieval, filtering, and rerank.
            trace_id: Non-blank query trace identifier.
            query: Optional user query used by the context optimizer. When the
                optimizer is absent this value is ignored.

        Returns:
            Stable response containing formatted evidence, citations, optional
            images, and an explicit empty-result marker.

        Raises:
            ValueError: If trace or source metadata is invalid, or image
                references violate their ingestion contract.

        Notes:
            Raw evidence is derived only from ``RetrievalResult.text``.
            Arbitrary metadata is never serialized, which prevents internal
            route/tool payloads from leaking into Agent-visible output.
        """

        citations = self._citation_builder.build(candidates, trace_id=trace_id)
        images = self._multimodal_assembler.assemble(candidates)
        raw_content = self._format_content(candidates)
        content = self._optimize_content(query=query, raw_content=raw_content)
        return KnowledgeHubResponse(
            content=content,
            citations=tuple(citations),
            images=tuple(images),
            trace_id=trace_id,
            is_empty=not candidates,
        )

    @staticmethod
    def _format_content(candidates: Sequence[RetrievalResult]) -> str:
        """Format ranked evidence as readable text instead of internal JSON.

        Args:
            candidates: Final ranked retrieval results.

        Returns:
            Numbered evidence blocks separated by blank lines, or an empty
            string when retrieval returned no candidates.
        """

        return "\n\n".join(
            f"[{index}] {candidate.text.strip()}"
            for index, candidate in enumerate(candidates, start=1)
        )

    def _optimize_content(self, *, query: str | None, raw_content: str) -> str:
        """Return optimized Agent context or a configured raw fallback.

        Args:
            query: User query supplied by ``QueryRuntime``.
            raw_content: Numbered evidence generated from final candidates.

        Returns:
            Optimized context when available, otherwise raw numbered evidence.

        Raises:
            RuntimeError: Re-raises optimizer failures when fallback is disabled.
        """

        if not raw_content or self._evidence_context_optimizer is None:
            return raw_content
        try:
            return self._evidence_context_optimizer.optimize(
                query=query or "",
                evidence=raw_content,
            )
        except Exception:
            if self._fallback_to_raw_content:
                return raw_content
            raise
