"""Adapt chat-model reranking to the provider-independent reranker contract.

``LLMReranker`` turns filtered retrieval candidates into a structured Prompt,
asks an injected ``BaseLLM`` client for an ordered JSON ranking, and returns new
``RetrievalResult`` objects. The adapter belongs to the pluggable ``libs`` layer:
it knows how to call an LLM and parse rerank output, but it does not perform
hybrid retrieval, metadata filtering, fallback orchestration, trace writing, or
answer generation.
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from typing import Any

from src.core.config import PromptTemplate, load_prompt
from src.core.errors import ProviderError
from src.core.types import RetrievalResult
from src.libs.llm.base_llm import BaseLLM, ChatMessage, LLMResponse
from src.libs.reranker.base_reranker import BaseReranker

DEFAULT_RERANK_PROMPT_PATH = "config/prompts/rerank_prompt.yaml"


class LLMReranker(BaseReranker):
    """Rerank filtered candidates by asking a chat model for JSON ordering.

    The class is intentionally provider-neutral. A concrete DeepSeek, OpenAI,
    fake, or future provider client is injected through the ``BaseLLM``
    contract. Unit tests therefore validate ranking behavior without network
    access, while runtime composition can still select the actual provider from
    configuration.
    """

    def __init__(
        self,
        *,
        llm_client: BaseLLM | None = None,
        prompt: PromptTemplate | None = None,
        prompt_path: str | None = None,
        model: str | None = None,
        llm_provider: str | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        """Configure the injected chat client and versioned Prompt.

        Args:
            llm_client: Provider-independent chat client. Tests pass a fake
                client; runtime orchestration may inject a settings-created
                client. If omitted, non-empty rerank calls raise
                ``ProviderError`` so the D9 controller can fallback safely.
            prompt: Already validated Prompt object. This is useful for tests or
                callers that preload configuration.
            prompt_path: RAG-root-relative or absolute Prompt file path used
                when ``prompt`` is not supplied.
            model: Optional operator-facing model label used before a provider
                response is available.
            llm_provider: Optional provider selector from rerank settings. It is
                recorded for diagnostics and does not create an LLM by itself.
            timeout_seconds: Future orchestration hint retained from settings so
                provider creation remains config-compatible. This adapter does
                not implement timeout handling directly.
        """

        self._llm_client = llm_client
        self._prompt = prompt or load_prompt(prompt_path or DEFAULT_RERANK_PROMPT_PATH)
        self._model = model or llm_provider or "llm"
        self._llm_provider = llm_provider
        self._timeout_seconds = timeout_seconds

    def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievalResult],
        *,
        top_k: int | None = None,
    ) -> list[RetrievalResult]:
        """Ask the LLM to reorder candidates and return defensive copies.

        Args:
            query: Original or rewritten user query.
            candidates: Already metadata-filtered retrieval results, usually in
                RRF order.
            top_k: Optional positive result limit.

        Returns:
            Candidate copies ordered by the LLM's returned ``candidate_id``
            sequence. Candidates omitted by the LLM are appended in their input
            order to preserve recall.

        Raises:
            ValueError: If query or ``top_k`` is invalid.
            ProviderError: If no LLM client is available, the provider call
                fails, or the returned JSON ranking is malformed.
        """

        if not query.strip():
            raise ValueError("Rerank query must not be blank")
        if top_k is not None and top_k <= 0:
            raise ValueError("top_k must be greater than zero")
        if not candidates:
            return []
        if self._llm_client is None:
            raise ProviderError(
                "LLM rerank client is not configured",
                context={
                    "provider": "llm",
                    "model": self._model,
                    "candidate_count": len(candidates),
                },
            )

        messages = self._build_messages(query=query, candidates=candidates)
        try:
            response = self._llm_client.chat(messages)
            ranking = self._parse_ranking(response=response, candidates=candidates)
        except ProviderError:
            raise
        except Exception as error:
            raise ProviderError(
                "LLM rerank failed",
                context={
                    "provider": "llm",
                    "model": self._model,
                    "candidate_count": len(candidates),
                },
                cause=error,
            ) from error

        results = self._apply_ranking(
            candidates=candidates,
            ranking=ranking,
            response=response,
        )
        return results if top_k is None else results[:top_k]

    async def async_rerank(
        self,
        query: str,
        candidates: Sequence[RetrievalResult],
        *,
        top_k: int | None = None,
    ) -> list[RetrievalResult]:
        """Ask the LLM to rerank candidates through the async chat contract.

        Args:
            query: Original or rewritten user query.
            candidates: Already metadata-filtered retrieval results, usually in
                RRF order.
            top_k: Optional positive result limit.

        Returns:
            Candidate copies ordered by the LLM's returned ranking, with omitted
            candidates appended in input order to preserve recall.

        Raises:
            ValueError: If query or ``top_k`` is invalid.
            ProviderError: If no LLM client is available, the async provider
                call fails, or returned JSON ranking is malformed.
        """

        if not query.strip():
            raise ValueError("Rerank query must not be blank")
        if top_k is not None and top_k <= 0:
            raise ValueError("top_k must be greater than zero")
        if not candidates:
            return []
        if self._llm_client is None:
            raise ProviderError(
                "LLM rerank client is not configured",
                context={
                    "provider": "llm",
                    "model": self._model,
                    "candidate_count": len(candidates),
                },
            )

        messages = self._build_messages(query=query, candidates=candidates)
        try:
            response = await self._llm_client.async_chat(messages)
            ranking = self._parse_ranking(response=response, candidates=candidates)
        except ProviderError:
            raise
        except Exception as error:
            raise ProviderError(
                "LLM rerank failed",
                context={
                    "provider": "llm",
                    "model": self._model,
                    "candidate_count": len(candidates),
                },
                cause=error,
            ) from error

        results = self._apply_ranking(
            candidates=candidates,
            ranking=ranking,
            response=response,
        )
        return results if top_k is None else results[:top_k]
    def _build_messages(
        self,
        *,
        query: str,
        candidates: Sequence[RetrievalResult],
    ) -> list[ChatMessage]:
        """Render the rerank Prompt with stable candidate identifiers.

        Args:
            query: User query already validated by ``rerank``.
            candidates: Filtered candidate list whose order should remain
                available as the fallback order.

        Returns:
            System and user messages suitable for any ``BaseLLM`` implementation.
        """

        candidate_payload = [
            {
                "candidate_id": candidate.chunk_id,
                "text": candidate.text,
                "metadata": candidate.metadata,
            }
            for candidate in candidates
        ]
        rendered_candidates = json.dumps(
            candidate_payload,
            ensure_ascii=False,
            indent=2,
        )
        return [
            ChatMessage(
                role="system",
                content=self._prompt.system_prompt.format(
                    query=query,
                    candidates=rendered_candidates,
                ),
            ),
            ChatMessage(
                role="user",
                content=self._prompt.user_prompt.format(
                    query=query,
                    candidates=rendered_candidates,
                ),
            ),
        ]

    def _parse_ranking(
        self,
        *,
        response: LLMResponse,
        candidates: Sequence[RetrievalResult],
    ) -> list[dict[str, Any]]:
        """Parse and validate the LLM's structured ranking response.

        Args:
            response: Provider-independent LLM response whose content should be
                a JSON array matching ``rerank_prompt.yaml``.
            candidates: Candidate list used to validate IDs and duplicates.

        Returns:
            Ranking items with ``candidate_id`` and optional score/reason data.

        Raises:
            ProviderError: If the response is not a JSON array, references an
                unknown candidate, repeats a candidate, or contains an invalid
                score.
        """

        candidate_ids = {candidate.chunk_id for candidate in candidates}
        try:
            parsed = json.loads(self._extract_json_payload(response.content))
        except json.JSONDecodeError as error:
            raise ProviderError(
                "LLM rerank failed",
                context={"provider": response.provider, "model": response.model},
                cause=error,
            ) from error

        if not isinstance(parsed, list):
            raise ProviderError(
                "LLM rerank failed",
                context={
                    "provider": response.provider,
                    "model": response.model,
                    "reason": "ranking_root_must_be_array",
                },
            )

        seen: set[str] = set()
        ranking: list[dict[str, Any]] = []
        for item in parsed:
            if not isinstance(item, dict):
                raise ProviderError(
                    "LLM rerank failed",
                    context={
                        "provider": response.provider,
                        "model": response.model,
                        "reason": "ranking_item_must_be_object",
                    },
                )
            candidate_id = item.get("candidate_id")
            if not isinstance(candidate_id, str) or not candidate_id.strip():
                raise ProviderError(
                    "LLM rerank failed",
                    context={
                        "provider": response.provider,
                        "model": response.model,
                        "reason": "candidate_id_required",
                    },
                )
            if candidate_id not in candidate_ids:
                raise ProviderError(
                    "LLM rerank failed",
                    context={
                        "provider": response.provider,
                        "model": response.model,
                        "candidate_id": candidate_id,
                        "reason": "unknown_candidate_id",
                    },
                )
            if candidate_id in seen:
                raise ProviderError(
                    "LLM rerank failed",
                    context={
                        "provider": response.provider,
                        "model": response.model,
                        "candidate_id": candidate_id,
                        "reason": "duplicate_candidate_id",
                    },
                )

            score = item.get("score")
            if score is not None:
                try:
                    score = float(score)
                except (TypeError, ValueError) as error:
                    raise ProviderError(
                        "LLM rerank failed",
                        context={
                            "provider": response.provider,
                            "model": response.model,
                            "candidate_id": candidate_id,
                            "reason": "invalid_score",
                        },
                        cause=error,
                    ) from error
                if not math.isfinite(score):
                    raise ProviderError(
                        "LLM rerank failed",
                        context={
                            "provider": response.provider,
                            "model": response.model,
                            "candidate_id": candidate_id,
                            "reason": "non_finite_score",
                        },
                    )

            seen.add(candidate_id)
            ranking.append(
                {
                    "candidate_id": candidate_id,
                    "score": score,
                    "reason": item.get("reason"),
                }
            )
        return ranking

    @staticmethod
    def _extract_json_payload(content: str) -> str:
        """Extract a JSON payload from plain text or fenced model output.

        Args:
            content: Raw LLM response content.

        Returns:
            Text that should parse as JSON.

        Notes:
            Providers sometimes wrap JSON in Markdown fences despite prompt
            instructions. This helper tolerates that common formatting drift
            without accepting unrelated prose around the array.
        """

        stripped = content.strip()
        if stripped.startswith("```"):
            lines = stripped.splitlines()
            if len(lines) >= 3 and lines[-1].strip() == "```":
                return "\n".join(lines[1:-1]).strip()
        return stripped

    def _apply_ranking(
        self,
        *,
        candidates: Sequence[RetrievalResult],
        ranking: list[dict[str, Any]],
        response: LLMResponse,
    ) -> list[RetrievalResult]:
        """Create result copies ordered by parsed LLM ranking.

        Args:
            candidates: Original filtered candidates.
            ranking: Validated LLM ranking items.
            response: Provider metadata used for rerank diagnostics.

        Returns:
            Reordered retrieval result copies. Ranked candidates receive LLM
            diagnostics; unmentioned candidates are appended unchanged except for
            the defensive deep copy.
        """

        by_id = {candidate.chunk_id: candidate for candidate in candidates}
        ranked_ids = {item["candidate_id"] for item in ranking}
        results = [
            self._copy_ranked_candidate(
                candidate=by_id[item["candidate_id"]],
                ranking_item=item,
                response=response,
            )
            for item in ranking
        ]
        results.extend(
            candidate.model_copy(deep=True)
            for candidate in candidates
            if candidate.chunk_id not in ranked_ids
        )
        return results

    def _copy_ranked_candidate(
        self,
        *,
        candidate: RetrievalResult,
        ranking_item: dict[str, Any],
        response: LLMResponse,
    ) -> RetrievalResult:
        """Return one candidate copy with LLM rerank diagnostics.

        Args:
            candidate: Original retrieval result selected by ``candidate_id``.
            ranking_item: Parsed LLM ranking entry for this candidate.
            response: Provider metadata used for trace-safe diagnostics.

        Returns:
            Retrieval result whose score is the LLM score when supplied, or the
            original retrieval score when the LLM omits a score.
        """

        score = ranking_item["score"]
        metadata = dict(candidate.metadata)
        metadata["rerank"] = {
            "provider": "llm",
            "model": response.model,
            "llm_provider": response.provider,
            "original_score": candidate.score,
        }
        reason = ranking_item.get("reason")
        if isinstance(reason, str) and reason.strip():
            metadata["rerank"]["reason"] = reason

        return RetrievalResult(
            chunk_id=candidate.chunk_id,
            text=candidate.text,
            score=candidate.score if score is None else score,
            metadata=metadata,
        )
