"""Provide the shared lexical analyzer used by BM25 indexing and querying.

The analyzer belongs to the dependency-light core layer because both offline
ingestion and online PostgreSQL retrieval must produce identical terms.
Keeping it outside either pipeline prevents circular imports and avoids ranking
drift caused by separate tokenization implementations.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import jieba
from pydantic import BaseModel, ConfigDict, Field, field_validator

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+|[\u3400-\u9fff]+")
_CJK_PATTERN = re.compile(r"^[\u3400-\u9fff]+$")

jieba.setLogLevel(logging.WARNING)


class BM25Candidate(BaseModel):
    """Represent one sparse candidate shared by in-memory and SQL indexes."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    chunk_id: str = Field(min_length=1)
    score: float = Field(allow_inf_nan=False)

    @field_validator("score")
    @classmethod
    def reject_negative_score(cls, value: float) -> float:
        """Require sparse relevance scores to remain non-negative.

        Args:
            value: Candidate BM25 relevance score.

        Returns:
            Float-normalized score.

        Raises:
            ValueError: If scoring produces a negative value.
        """

        score = float(value)
        if score < 0:
            raise ValueError("BM25 score must be non-negative")
        return score


def normalize_bm25_keywords(keywords: list[str] | str) -> list[str]:
    """Normalize raw text or processed keywords with the indexing analyzer.

    Args:
        keywords: Raw query text or ordered keyword values from
            ``ProcessedQuery``.

    Returns:
        Ordered unique lowercase terms produced by the shared BM25 analyzer.
    """

    if isinstance(keywords, str):
        return _ordered_unique(tokenize_bm25_text(keywords))
    terms: list[str] = []
    for keyword in keywords:
        terms.extend(tokenize_bm25_text(str(keyword)))
    return _ordered_unique(terms)


def tokenize_bm25_text(text: str) -> list[str]:
    """Tokenize content for both sparse ingestion and online query matching.

    Args:
        text: Chunk content or query text.

    Returns:
        Lowercase English/numeric spans and jieba-segmented CJK terms.
    """

    tokens: list[str] = []
    for match in _TOKEN_PATTERN.finditer(text):
        raw_token = match.group(0).lower()
        if _CJK_PATTERN.fullmatch(raw_token):
            tokens.extend(_tokenize_cjk_span(raw_token))
        else:
            tokens.append(raw_token)
    return tokens


def _tokenize_cjk_span(token: str) -> list[str]:
    """Segment one contiguous CJK span with jieba exact mode.

    Args:
        token: A contiguous Chinese text span extracted from chunk content or
            query text.

    Returns:
        Non-blank jieba terms in original order.
    """

    return [segment.strip() for segment in jieba.cut(token, cut_all=False) if segment.strip()]


def _ordered_unique(values: list[Any]) -> list[str]:
    """Deduplicate non-blank term values while preserving analyzer order."""

    return list(
        dict.fromkeys(
            str(value)
            for value in values
            if str(value).strip()
        )
    )
