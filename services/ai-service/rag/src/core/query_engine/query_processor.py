"""Normalize user questions into the stable input consumed by retrieval routes.

``QueryProcessor`` is the first online-query boundary. It validates raw input,
normalizes Unicode and whitespace, extracts ordered keywords for BM25, applies
settings-backed collection and Top-k defaults, and optionally delegates query
rewriting through a minimal injected interface.

This module does not classify business intent, instantiate an LLM, calculate
embeddings, query storage, or write trace records. Business routing belongs to
``IntentRouter`` after preprocessing. Rewrite failures deliberately fall back to
the normalized original query so optional model availability cannot disable
search.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core.config import RagSettings
from src.core.errors import RetrievalError

_WHITESPACE_PATTERN = re.compile(r"\s+")
_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.+-]*|[㐀-䶿一-鿿]+")

# The first-release vocabulary keeps deterministic Chinese keyword extraction
# useful without introducing a tokenizer dependency. Unknown Chinese runs are
# retained after filler removal, while common shopping concepts are separated
# so Sparse Route can match independently indexed terms.
_SHOPPING_PHRASES = (
    "人体工学键盘",
    "高性价比",
    "主动降噪",
    "连接稳定性",
    "商品链接",
    "无线耳机",
    "解压玩具",
    "通勤场景",
    "售后保障",
    "通话质量",
    "佩戴舒适度",
    "库存",
    "价格",
    "推荐",
    "对比",
    "比较",
    "挑选",
    "选购",
    "关注",
    "续航",
)
_QUESTION_FILLERS = (
    "如何",
    "怎么",
    "怎样",
    "帮我",
    "请问",
    "需要",
    "什么",
    "哪个",
    "哪款",
    "一款",
    "两款",
    "这款",
    "那款",
    "现在",
    "还有",
    "给我",
    "一下",
    "是否",
    "可以",
    "应该",
    "的",
    "是",
    "吗",
)
_ENGLISH_STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "for",
        "how",
        "is",
        "of",
        "please",
        "the",
        "to",
        "what",
        "which",
    }
)


class QueryRewriter(Protocol):
    """Define the optional query-rewrite dependency accepted by the processor."""

    def rewrite(self, query: str) -> str:
        """Return a retrieval-oriented rewrite of a normalized user query.

        Args:
            query: Validated and normalized original query.

        Returns:
            Rewritten query text. Blank output is treated as an unavailable
            rewrite and causes fallback to the supplied query.

        Raises:
            Exception: Provider adapters may raise transport, timeout, parsing,
                or model errors. ``QueryProcessor`` catches them and records a
                trace-friendly fallback reason.
        """


class ProcessedQuery(BaseModel):
    """Carry validated query state shared by retrieval and routing stages.

    Attributes:
        raw_query: Unmodified user input retained for trace and debugging.
        normalized_query: Canonical query used by retrieval. A successful
            rewrite replaces the normalized original text in this field.
        keywords: Immutable ordered unique terms consumed by Sparse Route.
        collection: Target knowledge collection selected by caller defaults or
            later refined by ``IntentRouter``.
        top_k: Requested final result count.
        rewrite_applied: Whether ``normalized_query`` came from the rewriter.
        rewrite_fallback_reason: Stable reason for a failed rewrite, or ``None``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    raw_query: str
    normalized_query: str = Field(min_length=1)
    keywords: tuple[str, ...] = Field(default_factory=tuple)
    collection: str = Field(min_length=1)
    top_k: int = Field(gt=0)
    rewrite_applied: bool = False
    rewrite_fallback_reason: str | None = None

    @field_validator("normalized_query", "collection")
    @classmethod
    def reject_blank_strings(cls, value: str) -> str:
        """Reject blank canonical query and collection values.

        Args:
            value: Candidate string supplied by ``QueryProcessor``.

        Returns:
            The original non-blank string.

        Raises:
            ValueError: If the value contains only whitespace.
        """

        if not value.strip():
            raise ValueError("ProcessedQuery string fields must not be blank")
        return value


class QueryProcessor:
    """Convert one raw question into a deterministic ``ProcessedQuery``."""

    def __init__(
        self,
        *,
        settings: RagSettings,
        rewriter: QueryRewriter | None = None,
    ) -> None:
        """Configure defaults and the optional rewrite boundary.

        Args:
            settings: Validated RAG settings providing rewrite enablement,
                default collection, and final Top-k.
            rewriter: Optional provider-independent rewrite adapter. The
                processor never creates or selects a concrete LLM itself.
        """

        self._settings = settings
        self._rewriter = rewriter

    def process(
        self,
        query: str,
        *,
        collection: str | None = None,
        top_k: int | None = None,
    ) -> ProcessedQuery:
        """Validate, normalize, optionally rewrite, and tokenize input.

        Args:
            query: Raw natural-language question from AImodel, MCP, or CLI.
            collection: Optional collection override. When omitted, retrieval
                settings provide the default collection.
            top_k: Optional positive final-result limit. When omitted,
                ``retrieval.final_top_k`` is used.

        Returns:
            An immutable ``ProcessedQuery`` suitable for routing and retrieval.

        Raises:
            RetrievalError: If query or collection is blank, top_k is not
                positive, or the input types cannot form a valid request.

        Notes:
            Rewriter exceptions and blank outputs do not raise. They produce a
            usable result with ``rewrite_fallback_reason`` set to a stable code.
        """

        if not isinstance(query, str):
            raise RetrievalError(
                "Query must be a string",
                context={"received_type": type(query).__name__},
            )
        normalized_original = _normalize_query(query)
        if not normalized_original:
            raise RetrievalError("Query must not be blank")

        if collection is not None and not isinstance(collection, str):
            raise RetrievalError(
                "Collection must be a string",
                context={"received_type": type(collection).__name__},
            )
        selected_collection = (
            self._settings.retrieval.filters.default_collection
            if collection is None
            else collection.strip()
        )
        if not selected_collection:
            raise RetrievalError("Collection must not be blank")

        selected_top_k = self._settings.retrieval.final_top_k if top_k is None else top_k
        if isinstance(selected_top_k, bool) or not isinstance(selected_top_k, int):
            raise RetrievalError(
                "top_k must be an integer",
                context={"received_type": type(selected_top_k).__name__},
            )
        if selected_top_k <= 0:
            raise RetrievalError("top_k must be greater than zero")

        retrieval_query, rewrite_applied, fallback_reason = self._rewrite(normalized_original)
        return ProcessedQuery(
            raw_query=query,
            normalized_query=retrieval_query,
            keywords=_extract_keywords(retrieval_query),
            collection=selected_collection,
            top_k=selected_top_k,
            rewrite_applied=rewrite_applied,
            rewrite_fallback_reason=fallback_reason,
        )

    def _rewrite(self, normalized_query: str) -> tuple[str, bool, str | None]:
        """Apply optional rewrite and degrade to the normalized source query.

        Args:
            normalized_query: Valid non-blank query after local normalization.

        Returns:
            Final retrieval query, applied flag, and optional fallback code.
        """

        if not self._settings.retrieval.query_rewrite_enabled or self._rewriter is None:
            return normalized_query, False, None
        try:
            rewritten = self._rewriter.rewrite(normalized_query)
        except Exception:
            return normalized_query, False, "rewriter_error"
        if not isinstance(rewritten, str) or not rewritten.strip():
            return normalized_query, False, "blank_rewrite"
        return _normalize_query(rewritten), True, None


def _normalize_query(query: str) -> str:
    """Normalize Unicode compatibility forms and collapse all whitespace."""

    normalized = unicodedata.normalize("NFKC", query)
    return _WHITESPACE_PATTERN.sub(" ", normalized).strip()


def _extract_keywords(query: str) -> tuple[str, ...]:
    """Extract ordered unique English terms and Chinese shopping concepts."""

    keywords: list[str] = []
    for token in _TOKEN_PATTERN.findall(query):
        if token.isascii():
            normalized = token.casefold()
            if normalized not in _ENGLISH_STOP_WORDS:
                _append_unique(keywords, normalized)
            continue
        for keyword in _extract_chinese_keywords(token):
            _append_unique(keywords, keyword)
    return tuple(keywords)


def _extract_chinese_keywords(token: str) -> list[str]:
    """Separate known shopping concepts and retain meaningful unknown text."""

    matches: list[tuple[int, int, str]] = []
    occupied = [False] * len(token)
    for phrase in _SHOPPING_PHRASES:
        start = token.find(phrase)
        while start >= 0:
            end = start + len(phrase)
            if not any(occupied[start:end]):
                matches.append((start, end, phrase))
                occupied[start:end] = [True] * (end - start)
            start = token.find(phrase, start + 1)

    residual_parts: list[tuple[int, str]] = []
    cursor = 0
    while cursor < len(token):
        if occupied[cursor]:
            cursor += 1
            continue
        end = cursor + 1
        while end < len(token) and not occupied[end]:
            end += 1
        residual = token[cursor:end]
        for filler in _QUESTION_FILLERS:
            residual = residual.replace(filler, " ")
        for part in residual.split():
            if len(part) >= 2:
                residual_parts.append((cursor, part))
        cursor = end

    ordered = [(start, phrase) for start, _, phrase in matches]
    ordered.extend(residual_parts)
    ordered.sort(key=lambda item: item[0])
    return [phrase for _, phrase in ordered]


def _append_unique(values: list[str], value: str) -> None:
    """Append a non-blank keyword only when its normalized value is new."""

    normalized = value.strip()
    if normalized and normalized not in values:
        values.append(normalized)
