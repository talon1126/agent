"""Build the public knowledge-hub response from ranked retrieval results.

``KnowledgeHubResponseBuilder`` is the final core-layer projection before MCP,
CLI, Dashboard, or AImodel adapters serialize data. It formats retrieved text,
delegates grounded citations to ``CitationBuilder``, and delegates optional
image resolution to ``MultimodalAssembler``. The resulting model intentionally
contains no route diagnostics, vectors, provider payloads, or internal tool
JSON.

This module does not generate a natural-language answer. The returned content
is ranked evidence that an Agent or caller can summarize while preserving
citations.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core.response.citation_builder import CitationBuilder
from src.core.response.multimodal_assembler import (
    MultimodalAssembler,
    ResponseImage,
)
from src.core.types import Citation, RetrievalResult


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
    ) -> None:
        """Configure replaceable response-layer collaborators.

        Args:
            citation_builder: Source attribution component. ``None`` uses the
                default grounded ``CitationBuilder``.
            multimodal_assembler: Optional image resolver. ``None`` creates a
                text-only assembler so RAG remains usable without image
                storage wiring.
        """

        self._citation_builder = citation_builder or CitationBuilder()
        self._multimodal_assembler = (
            multimodal_assembler or MultimodalAssembler()
        )

    def build(
        self,
        candidates: Sequence[RetrievalResult],
        *,
        trace_id: str,
    ) -> KnowledgeHubResponse:
        """Build one immutable public response from final ranked candidates.

        Args:
            candidates: Results after hybrid retrieval, filtering, and rerank.
            trace_id: Non-blank query trace identifier.

        Returns:
            Stable response containing formatted evidence, citations, optional
            images, and an explicit empty-result marker.

        Raises:
            ValueError: If trace or source metadata is invalid, or image
                references violate their ingestion contract.

        Notes:
            Content is derived only from ``RetrievalResult.text``. Arbitrary
            metadata is never serialized, which prevents internal route/tool
            payloads from leaking into Agent-visible output.
        """

        citations = self._citation_builder.build(candidates, trace_id=trace_id)
        images = self._multimodal_assembler.assemble(candidates)
        content = self._format_content(candidates)
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
