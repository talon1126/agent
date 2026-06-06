"""Build and query sparse BM25 statistics for transformed chunks.

``BM25Indexer`` is the C7 sparse-indexing boundary. It tokenizes chunk text,
builds term frequency and inverted-index structures, and returns ranked
``chunk_id`` candidates for query keywords. It intentionally stays in memory:
PostgreSQL persistence, batch execution, and Sparse Route hydration are owned
by later tasks.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.core.types import Chunk

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+|[\u3400-\u9fff]+")
_CJK_PATTERN = re.compile(r"^[\u3400-\u9fff]+$")


class BM25Candidate(BaseModel):
    """Represent one sparse retrieval candidate produced by BM25."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    chunk_id: str = Field(min_length=1)
    score: float = Field(allow_inf_nan=False)

    @field_validator("score")
    @classmethod
    def reject_negative_score(cls, value: float) -> float:
        """Require BM25 scores to remain non-negative.

        Args:
            value: Candidate BM25 score.

        Returns:
            The float-normalized score.

        Raises:
            ValueError: If a scoring bug produces a negative value.
        """

        score = float(value)
        if score < 0:
            raise ValueError("BM25 score must be non-negative")
        return score


class BM25IndexResult(BaseModel):
    """Expose sparse index statistics for storage and diagnostics."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    chunk_count: int = Field(ge=0)
    average_document_length: float = Field(ge=0)
    document_lengths: dict[str, int] = Field(default_factory=dict)
    term_frequencies: dict[str, dict[str, int]] = Field(default_factory=dict)
    term_document_frequency: dict[str, int] = Field(default_factory=dict)
    inverted_index: dict[str, dict[str, int]] = Field(default_factory=dict)


class BM25Indexer:
    """Build an in-memory BM25 sparse index and rank keyword candidates."""

    def __init__(self, *, k1: float = 1.5, b: float = 0.75) -> None:
        """Configure BM25 scoring parameters.

        Args:
            k1: Term-frequency saturation factor.
            b: Length-normalization factor.

        Raises:
            ValueError: If either scoring parameter is outside a usable range.
        """

        if k1 <= 0:
            raise ValueError("BM25 k1 must be positive")
        if not 0 <= b <= 1:
            raise ValueError("BM25 b must be between 0 and 1")
        self._k1 = k1
        self._b = b
        self._index = BM25IndexResult(chunk_count=0, average_document_length=0.0)

    def index(self, chunks: list[Chunk]) -> BM25IndexResult:
        """Build sparse statistics from a complete chunk list.

        Args:
            chunks: Ordered chunks to index. Calling ``index`` replaces any
                previous in-memory index state.

        Returns:
            A ``BM25IndexResult`` containing term frequencies, document
            frequencies, inverted index postings, document lengths, and average
            document length.
        """

        term_frequencies: dict[str, dict[str, int]] = {}
        document_lengths: dict[str, int] = {}
        postings: dict[str, dict[str, int]] = defaultdict(dict)

        for chunk in chunks:
            tokens = _tokenize(chunk.text)
            counts = Counter(tokens)
            document_lengths[chunk.id] = len(tokens)
            term_frequencies[chunk.id] = dict(counts)
            for term, frequency in counts.items():
                postings[term][chunk.id] = frequency

        average_length = (
            sum(document_lengths.values()) / len(document_lengths)
            if document_lengths
            else 0.0
        )
        self._index = BM25IndexResult(
            chunk_count=len(chunks),
            average_document_length=average_length,
            document_lengths=document_lengths,
            term_frequencies=term_frequencies,
            term_document_frequency={
                term: len(chunk_frequencies)
                for term, chunk_frequencies in postings.items()
            },
            inverted_index={
                term: dict(chunk_frequencies)
                for term, chunk_frequencies in postings.items()
            },
        )
        return self._index.model_copy(deep=True)

    def query(self, keywords: list[str] | str, *, top_k: int) -> list[BM25Candidate]:
        """Return top BM25 candidates for normalized query keywords.

        Args:
            keywords: Query terms from ``ProcessedQuery.keywords`` or a raw
                string that should be tokenized with the same analyzer.
            top_k: Maximum number of candidates to return.

        Returns:
            Ranked sparse candidates containing only ``chunk_id`` and native
            BM25 score. Sparse Route will hydrate chunk text and metadata later.

        Raises:
            ValueError: If ``top_k`` is not positive.
        """

        if top_k <= 0:
            raise ValueError("top_k must be positive")

        terms = _normalize_keywords(keywords)
        if not terms or self._index.chunk_count == 0:
            return []

        scores: dict[str, float] = defaultdict(float)
        for term in terms:
            postings = self._index.inverted_index.get(term)
            if not postings:
                continue
            idf = self._idf(term)
            for chunk_id, frequency in postings.items():
                scores[chunk_id] += self._score_term(
                    term_frequency=frequency,
                    document_length=self._index.document_lengths[chunk_id],
                    idf=idf,
                )

        ranked = sorted(
            (
                BM25Candidate(chunk_id=chunk_id, score=score)
                for chunk_id, score in scores.items()
                if score > 0
            ),
            key=lambda candidate: (-candidate.score, candidate.chunk_id),
        )
        return ranked[:top_k]

    def _idf(self, term: str) -> float:
        """Compute BM25 inverse document frequency for one term.

        Args:
            term: Normalized token present in the inverted index.

        Returns:
            Non-negative BM25 IDF value.
        """

        document_frequency = self._index.term_document_frequency[term]
        numerator = self._index.chunk_count - document_frequency + 0.5
        denominator = document_frequency + 0.5
        return math.log(1 + numerator / denominator)

    def _score_term(
        self,
        *,
        term_frequency: int,
        document_length: int,
        idf: float,
    ) -> float:
        """Compute one term contribution for one chunk.

        Args:
            term_frequency: Number of times the term appears in the chunk.
            document_length: Token count for the chunk.
            idf: Precomputed inverse document frequency for the term.

        Returns:
            BM25 contribution for this term/chunk pair.
        """

        if self._index.average_document_length == 0:
            return 0.0
        denominator = term_frequency + self._k1 * (
            1 - self._b + self._b * document_length / self._index.average_document_length
        )
        return idf * (term_frequency * (self._k1 + 1)) / denominator


def _normalize_keywords(keywords: list[str] | str) -> list[str]:
    """Normalize query keywords using the same analyzer as indexing.

    Args:
        keywords: Either a raw query string or pre-extracted keyword list.

    Returns:
        Ordered unique normalized terms.
    """

    if isinstance(keywords, str):
        return _ordered_unique(_tokenize(keywords))
    terms: list[str] = []
    for keyword in keywords:
        terms.extend(_tokenize(str(keyword)))
    return _ordered_unique(terms)


def _tokenize(text: str) -> list[str]:
    """Tokenize English, numbers, and contiguous CJK text for sparse indexing.

    Args:
        text: Raw chunk text or query text.

    Returns:
        Lowercase tokens with blanks removed. English and numeric spans are
        emitted as whole tokens. Contiguous CJK spans are expanded into
        deterministic character n-grams so Chinese product terms can match
        inside longer unsegmented sentences without adding a heavy tokenizer
        dependency in C7.
    """

    tokens: list[str] = []
    for match in _TOKEN_PATTERN.finditer(text):
        raw_token = match.group(0).lower()
        if _CJK_PATTERN.fullmatch(raw_token):
            tokens.extend(_expand_cjk_token(raw_token))
        else:
            tokens.append(raw_token)
    return tokens


def _expand_cjk_token(token: str) -> list[str]:
    """Expand one continuous CJK span into searchable fallback tokens.

    Args:
        token: A non-empty string containing only CJK characters.

    Returns:
        Ordered tokens containing the original span plus 2-gram and 3-gram
        windows. The original span preserves exact full-sentence matches while
        n-grams make shorter Chinese query terms reusable for BM25 matching.
    """

    if len(token) <= 1:
        return [token]
    tokens = [token]
    for gram_size in (2, 3):
        if len(token) < gram_size:
            continue
        tokens.extend(
            token[start : start + gram_size]
            for start in range(0, len(token) - gram_size + 1)
        )
    return tokens


def _ordered_unique(values: list[Any]) -> list[str]:
    """Deduplicate terms while preserving their original order.

    Args:
        values: Candidate token values.

    Returns:
        Ordered unique non-blank string terms.
    """

    return list(dict.fromkeys(str(value) for value in values if str(value).strip()))
