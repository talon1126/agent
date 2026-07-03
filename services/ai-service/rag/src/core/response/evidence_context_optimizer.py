"""Optimize ranked evidence into Agent-ready final context.

``EvidenceContextOptimizer`` sits at the end of the response layer. Retrieval
has already selected, filtered, and reranked chunks; this component only
reformats those ranked evidence blocks so AImodel can consume them directly as
context. It deliberately does not generate a final user-facing answer, call
product APIs, create citations, or mutate retrieval results.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping
from typing import Any

from src.core.config import PromptTemplate, load_prompt
from src.core.errors import ProviderError
from src.libs.llm.base_llm import BaseLLM, ChatMessage

DEFAULT_EVIDENCE_CONTEXT_PROMPT_PATH = "config/prompts/evidence_context_prompt.yaml"
_EVIDENCE_LABEL_RE = re.compile(r"\[(\d+)\]")


class EvidenceContextOptimizer:
    """Use an LLM Prompt to create final context from numbered evidence.

    Args:
        llm_client: Provider-independent chat client. Runtime composition uses
            ``LLMFactory`` while unit tests pass fakes.
        prompt: Optional already-loaded prompt template.
        prompt_path: Prompt path used when ``prompt`` is omitted.

    Notes:
        The optimizer keeps source alignment by requiring every numbered label
        found in the raw evidence to appear in the generated content. This
        prevents a model from producing polished text that can no longer be
        cited by ``query_result.contexts``.
    """

    def __init__(
        self,
        *,
        llm_client: BaseLLM | None = None,
        prompt: PromptTemplate | None = None,
        prompt_path: str | None = None,
    ) -> None:
        """Configure the LLM client and versioned evidence-context Prompt.

        Args:
            llm_client: Chat model used for context preparation. ``None`` makes
                non-empty optimization calls fail with ``ProviderError`` so the
                response builder can apply its configured fallback.
            prompt: Preloaded prompt, mainly for tests.
            prompt_path: RAG-root-relative or absolute prompt path.
        """

        self._llm_client = llm_client
        self._prompt = prompt or load_prompt(prompt_path or DEFAULT_EVIDENCE_CONTEXT_PROMPT_PATH)

    def optimize(self, *, query: str, evidence: str) -> str:
        """Return Agent-ready context generated from numbered evidence.

        Args:
            query: User query used to focus the context. Blank queries are
                rejected because the Prompt must know what the Agent needs.
            evidence: Numbered evidence blocks such as ``[1] ...``.

        Returns:
            Optimized context text preserving every evidence label.

        Raises:
            ValueError: If query/evidence is blank, the provider returns empty
                content, or any evidence label is dropped.
            ProviderError: If no LLM client is configured or the provider call
                fails.
        """

        normalized_query = _require_text(query, field_name="query")
        normalized_evidence = _require_text(evidence, field_name="evidence")
        if self._llm_client is None:
            raise ProviderError(
                "Evidence context optimizer LLM client is not configured",
                context={"prompt": self._prompt.name},
            )

        try:
            response = self._llm_client.chat(
                [
                    ChatMessage(
                        role="system",
                        content=self._prompt.system_prompt.format(
                            query=normalized_query,
                            evidence=normalized_evidence,
                        ),
                    ),
                    ChatMessage(
                        role="user",
                        content=self._prompt.user_prompt.format(
                            query=normalized_query,
                            evidence=normalized_evidence,
                        ),
                    ),
                ]
            )
        except ProviderError:
            raise
        except Exception as error:
            raise ProviderError(
                "Evidence context optimization failed",
                context={"prompt": self._prompt.name},
                cause=error,
            ) from error

        content = _content_from_response(response.content)
        _require_evidence_labels(normalized_evidence, content)
        return content


def _content_from_response(response_text: str) -> str:
    """Extract the strict JSON ``content`` field returned by the Prompt.

    Args:
        response_text: Raw provider response text.

    Returns:
        Non-empty optimized content.

    Raises:
        ValueError: If the response is not a JSON object with non-empty
            ``content``.
    """

    try:
        payload = json.loads(_strip_markdown_fence(response_text))
    except json.JSONDecodeError as error:
        raise ValueError("Evidence context optimizer response must be JSON") from error
    if not isinstance(payload, Mapping):
        raise ValueError("Evidence context optimizer response must be a JSON object")
    return _require_text(payload.get("content"), field_name="content")


def _strip_markdown_fence(value: str) -> str:
    """Remove common code fences while keeping strict JSON parsing at the edge."""

    stripped = value.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            return "\n".join(lines[1:-1]).strip()
    return stripped


def _require_evidence_labels(evidence: str, content: str) -> None:
    """Require optimized content to preserve evidence labels without duplication.

    Args:
        evidence: Raw numbered evidence blocks.
        content: Optimized context returned by the provider.

    Raises:
        ValueError: If a label such as ``[2]`` exists in evidence but is absent
            from optimized content, or if a provider repeats a label more
            times than it appeared in the raw ranked evidence.
    """

    evidence_counts = Counter(
        f"[{label}]" for label in _EVIDENCE_LABEL_RE.findall(evidence)
    )
    content_counts = Counter(
        f"[{label}]" for label in _EVIDENCE_LABEL_RE.findall(content)
    )
    required_labels = set(evidence_counts)
    missing = sorted(label for label in required_labels if label not in content)
    if missing:
        raise ValueError(f"Optimized context dropped evidence label(s): {missing}")
    duplicated = sorted(
        label
        for label, count in content_counts.items()
        if count > evidence_counts.get(label, 0)
    )
    if duplicated:
        raise ValueError(f"Optimized context repeated evidence label(s): {duplicated}")


def _require_text(value: Any, *, field_name: str) -> str:
    """Return a stripped non-empty string field.

    Args:
        value: Candidate text.
        field_name: Name used in validation messages.

    Returns:
        Stripped text.

    Raises:
        ValueError: If ``value`` is not a non-empty string.
    """

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value.strip()
