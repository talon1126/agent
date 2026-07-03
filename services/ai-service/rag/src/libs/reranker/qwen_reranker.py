"""Adapt Qwen reranker models to the provider-independent reranker contract."""

from __future__ import annotations

import asyncio
import math
import threading
from collections.abc import Callable, Sequence
from typing import Protocol

from src.core.errors import ProviderError
from src.core.types import RetrievalResult
from src.libs.reranker.base_reranker import BaseReranker

DEFAULT_QWEN_RERANK_INSTRUCTION = (
    "Given a web search query, retrieve relevant passages that answer the query"
)


class QwenScorer(Protocol):
    """Describe the Qwen reranker scoring contract."""

    def score(
        self,
        pairs: list[tuple[str, str]],
        *,
        instruction: str,
        max_length: int,
        batch_size: int,
    ) -> Sequence[float]:
        """Return normalized 0-1 relevance scores for query-document pairs."""


QwenLoader = Callable[[str, str | None], QwenScorer]


class QwenModelCache:
    """Cache Qwen reranker scorer instances by model and device per process."""

    _lock = threading.Lock()
    _cache: dict[tuple[str, str | None], QwenScorer] = {}
    _loader: QwenLoader | None = None

    @classmethod
    def configure_loader(cls, loader: QwenLoader | None) -> None:
        """Override the scorer loader for tests or restore the default loader."""

        with cls._lock:
            cls._loader = loader

    @classmethod
    def clear(cls) -> None:
        """Clear cached scorers without changing the configured loader."""

        with cls._lock:
            cls._cache.clear()

    @classmethod
    def get_scorer(cls, model: str, device: str | None) -> QwenScorer:
        """Return one cached scorer for a ``model + device`` key."""

        key = (model, device)
        with cls._lock:
            cached = cls._cache.get(key)
            if cached is not None:
                return cached
            loader = cls._loader
        scorer = (
            loader(model, device) if loader is not None else TransformersQwenScorer(model, device)
        )
        with cls._lock:
            return cls._cache.setdefault(key, scorer)

    @classmethod
    def warmup(cls, model: str, device: str | None) -> None:
        """Load and run a tiny prediction through the cached scorer."""

        scorer = cls.get_scorer(model, device)
        scorer.score(
            [("warmup query", "warmup document")],
            instruction=DEFAULT_QWEN_RERANK_INSTRUCTION,
            max_length=512,
            batch_size=1,
        )


class TransformersQwenScorer:
    """Score Qwen reranker pairs using yes/no token probabilities."""

    def __init__(self, model: str, device: str | None) -> None:
        """Load tokenizer and causal LM for Qwen reranking."""

        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        self._torch = torch
        self._tokenizer = AutoTokenizer.from_pretrained(model, padding_side="left")
        dtype = torch.float16 if device and device.startswith("cuda") else torch.float32
        self._model = AutoModelForCausalLM.from_pretrained(
            model,
            torch_dtype=dtype,
        ).eval()
        if device:
            self._model.to(device)
        self._device = device
        self._false_token_id = self._tokenizer("no", add_special_tokens=False).input_ids[0]
        self._true_token_id = self._tokenizer("yes", add_special_tokens=False).input_ids[0]
        self._prefix = (
            "<|im_start|>system\n"
            "Judge whether the Document meets the requirements based on the Query "
            'and the Instruct provided. Note that the answer can only be "yes" or "no".'
            "<|im_end|>\n<|im_start|>user\n"
        )
        self._suffix = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
        self._prefix_tokens = self._tokenizer.encode(self._prefix, add_special_tokens=False)
        self._suffix_tokens = self._tokenizer.encode(self._suffix, add_special_tokens=False)

    def score(
        self,
        pairs: list[tuple[str, str]],
        *,
        instruction: str,
        max_length: int,
        batch_size: int,
    ) -> list[float]:
        """Return normalized relevance scores for each pair."""

        if not pairs:
            return []
        scores: list[float] = []
        effective_max_length = max(
            1, max_length - len(self._prefix_tokens) - len(self._suffix_tokens)
        )
        for start in range(0, len(pairs), batch_size):
            batch_pairs = pairs[start : start + batch_size]
            texts = [
                self._format_instruction(instruction, query, document)
                for query, document in batch_pairs
            ]
            encoded = self._tokenizer(
                texts,
                padding=False,
                truncation="longest_first",
                return_attention_mask=False,
                max_length=effective_max_length,
            )
            encoded["input_ids"] = [
                self._prefix_tokens + input_ids + self._suffix_tokens
                for input_ids in encoded["input_ids"]
            ]
            padded = self._tokenizer.pad(
                encoded,
                padding=True,
                return_tensors="pt",
                max_length=max_length,
            )
            if self._device:
                padded = {key: value.to(self._device) for key, value in padded.items()}
            with self._torch.no_grad():
                logits = self._model(**padded).logits[:, -1, :]
            false_logits = logits[:, self._false_token_id]
            true_logits = logits[:, self._true_token_id]
            yes_no_logits = self._torch.stack([false_logits, true_logits], dim=1)
            probabilities = self._torch.nn.functional.log_softmax(yes_no_logits, dim=1)
            scores.extend(
                float(score) for score in probabilities[:, 1].exp().detach().cpu().tolist()
            )
        return scores

    @staticmethod
    def _format_instruction(instruction: str, query: str, document: str) -> str:
        """Build the Qwen reranker instruction text for one pair."""

        return f"<Instruct>: {instruction}\n<Query>: {query}\n<Document>: {document}"


class QwenReranker(BaseReranker):
    """Score filtered candidates with a Qwen reranker and reorder them."""

    def __init__(
        self,
        *,
        model: str,
        device: str | None = None,
        max_length: int = 8192,
        batch_size: int = 4,
        instruction: str = DEFAULT_QWEN_RERANK_INSTRUCTION,
        scorer: QwenScorer | None = None,
    ) -> None:
        """Configure the Qwen reranker adapter."""

        if not model.strip():
            raise ValueError("Qwen reranker model must not be blank")
        if max_length <= 0:
            raise ValueError("max_length must be greater than zero")
        if batch_size <= 0:
            raise ValueError("batch_size must be greater than zero")
        self._model = model
        self._device = device
        self._max_length = max_length
        self._batch_size = batch_size
        self._instruction = instruction
        self._scorer = scorer

    def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievalResult],
        *,
        top_k: int | None = None,
    ) -> list[RetrievalResult]:
        """Score query-candidate pairs and return ranked candidate copies."""

        if not query.strip():
            raise ValueError("Rerank query must not be blank")
        if top_k is not None and top_k <= 0:
            raise ValueError("top_k must be greater than zero")
        if not candidates:
            return []

        pairs = [(query, candidate.text) for candidate in candidates]
        try:
            scores = [
                float(score)
                for score in self._get_scorer().score(
                    pairs,
                    instruction=self._instruction,
                    max_length=self._max_length,
                    batch_size=self._batch_size,
                )
            ]
            self._validate_scores(scores, expected_count=len(candidates))
        except Exception as error:
            raise ProviderError(
                "Qwen rerank failed",
                context={
                    "provider": "qwen",
                    "model": self._model,
                    "candidate_count": len(candidates),
                },
                cause=error,
            ) from error

        indexed_results = [
            (index, self._with_rerank_score(candidate, score))
            for index, (candidate, score) in enumerate(zip(candidates, scores, strict=True))
        ]
        indexed_results.sort(key=lambda item: (-item[1].score, item[0]))
        results = [result for _, result in indexed_results]
        return results if top_k is None else results[:top_k]

    async def async_rerank(
        self,
        query: str,
        candidates: Sequence[RetrievalResult],
        *,
        top_k: int | None = None,
    ) -> list[RetrievalResult]:
        """Run Qwen scoring in a worker thread."""

        return await asyncio.to_thread(self.rerank, query, candidates, top_k=top_k)

    def warmup(self) -> None:
        """Preload and run one tiny prediction through the Qwen reranker."""

        self._get_scorer().score(
            [("warmup query", "warmup document")],
            instruction=self._instruction,
            max_length=min(self._max_length, 512),
            batch_size=1,
        )

    def _get_scorer(self) -> QwenScorer:
        """Return an injected scorer or lazily load a cached Qwen scorer."""

        if self._scorer is not None:
            return self._scorer
        try:
            self._scorer = QwenModelCache.get_scorer(self._model, self._device)
        except Exception as error:
            raise ProviderError(
                "Unable to load Qwen reranker",
                context={
                    "provider": "qwen",
                    "model": self._model,
                    "device": self._device,
                },
                cause=error,
            ) from error
        return self._scorer

    @staticmethod
    def _validate_scores(scores: list[float], *, expected_count: int) -> None:
        """Validate scorer output before ranking candidates."""

        if len(scores) != expected_count:
            raise ValueError("Qwen score count must match candidate count")
        if any(not math.isfinite(score) for score in scores):
            raise ValueError("Qwen scores must be finite")
        if any(score < 0.0 or score > 1.0 for score in scores):
            raise ValueError("Qwen scores must be normalized between 0 and 1")

    def _with_rerank_score(
        self,
        candidate: RetrievalResult,
        score: float,
    ) -> RetrievalResult:
        """Return a candidate copy with Qwen rerank score diagnostics."""

        metadata = dict(candidate.metadata)
        metadata["rerank"] = {
            "provider": "qwen",
            "model": self._model,
            "original_score": candidate.score,
        }
        return RetrievalResult(
            chunk_id=candidate.chunk_id,
            text=candidate.text,
            score=score,
            metadata=metadata,
        )
