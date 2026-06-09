"""Generate document-level summaries before chunk-level transforms.

The summarizer is an ingestion-stage adapter, not a Loader responsibility.
Loaders produce canonical ``Document`` objects from source files, while this
module optionally calls an LLM to attach a concise ``Document.summary`` that
later chunk rewrite prompts can use as global context.
"""

from __future__ import annotations

import json
from copy import deepcopy
from hashlib import sha256
from typing import Any

from src.core.config import PromptTemplate
from src.core.errors import IngestionError
from src.core.types import Document
from src.libs.llm import BaseLLM, ChatMessage


class DocumentSummarizer:
    """Attach one concise semantic summary to a loaded document."""

    def __init__(
        self,
        *,
        llm: BaseLLM,
        prompt: PromptTemplate,
        max_document_chars: int = 12000,
    ) -> None:
        """Configure the LLM-backed summary step.

        Args:
            llm: Provider-independent chat client selected by ``LLMFactory`` or
                injected by tests.
            prompt: Validated document-summary Prompt loaded from config.
            max_document_chars: Maximum number of source-text characters sent
                to the model. Large documents are summarized from a stable
                leading window so the prompt remains bounded.

        Raises:
            ValueError: If ``max_document_chars`` cannot include useful text.
        """

        if max_document_chars <= 0:
            raise ValueError("max_document_chars must be greater than zero")
        self._llm = llm
        self._prompt = prompt
        self._max_document_chars = max_document_chars
        self._version = f"{prompt.name}:{prompt.version}"

    def summarize(
        self,
        document: Document,
        *,
        context: dict[str, Any] | None = None,
    ) -> Document:
        """Return a document copy with a top-level summary.

        Args:
            document: Loaded canonical document. The input object is never
                mutated.
            context: Optional trace/runtime metadata. It is currently reserved
                for future provider routing and trace enrichment.

        Returns:
            A deep-copied ``Document`` with ``summary`` populated. Documents
            already summarized by this prompt version are returned as deep
            copies without another LLM call.

        Raises:
            IngestionError: If prompt rendering, provider execution, or response
                validation fails.
        """

        del context
        generation = document.metadata.get("summary_generation")
        if (
            document.summary
            and isinstance(generation, dict)
            and generation.get("version") == self._version
            and generation.get("document_hash") == _content_hash(document.text)
        ):
            return document.model_copy(deep=True)

        try:
            response = self._llm.chat(
                [
                    ChatMessage(role="system", content=self._prompt.system_prompt),
                    ChatMessage(
                        role="user",
                        content=self._prompt.user_prompt.format(
                            document_text=_bounded_document_text(
                                document.text,
                                limit=self._max_document_chars,
                            ),
                            metadata=json.dumps(
                                document.metadata,
                                ensure_ascii=False,
                                sort_keys=True,
                            ),
                        ),
                    ),
                ]
            )
            summary = response.content.strip()
            if not summary:
                raise ValueError("Summary provider returned blank content")
        except Exception as error:
            raise IngestionError(
                "Unable to summarize document",
                context={
                    "operation": "summarize_document",
                    "document_id": document.id,
                    "prompt_version": self._version,
                },
                cause=error,
            ) from error

        metadata = deepcopy(document.metadata)
        metadata["summary_generation"] = {
            "version": self._version,
            "provider": response.provider,
            "model": response.model,
            "document_hash": _content_hash(document.text),
            "truncated": len(document.text) > self._max_document_chars,
        }
        return document.model_copy(
            update={"summary": summary, "metadata": metadata},
            deep=True,
        )


def _bounded_document_text(text: str, *, limit: int) -> str:
    """Return a stable prompt window for long documents.

    Args:
        text: Canonical document text.
        limit: Maximum number of characters to return.

    Returns:
        The original text when it fits the limit, otherwise a prefix plus a
        clear truncation marker for the model.
    """

    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n\n[TRUNCATED_FOR_DOCUMENT_SUMMARY]"


def _content_hash(text: str) -> str:
    """Return the SHA256 digest used for summary idempotency."""

    return sha256(text.encode("utf-8")).hexdigest()
