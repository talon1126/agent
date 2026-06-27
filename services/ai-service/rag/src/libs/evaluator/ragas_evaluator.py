"""Expose Ragas through the pluggable evaluator interface.

The project-owned Ragas adapter lives in ``src.observability.evaluation`` so it
can share the evaluation runner and Dashboard contracts. This thin libs-layer
class makes the same implementation available through ``EvaluatorFactory``,
which is the single component creation boundary used by services and scripts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.core.config import RagSettings
from src.libs.embedding.embedding_factory import EmbeddingFactory
from src.libs.evaluator.base_evaluator import BaseEvaluator
from src.libs.llm.llm_factory import LLMFactory
from src.observability.evaluation.ragas_adapter import (
    RagasEvaluateFn,
    RagasEvaluator,
)


class RagasEvaluatorClient(BaseEvaluator):
    """Delegate evaluator calls to the observability Ragas adapter.

    Args:
        metric_names: Optional Ragas metric names. ``None`` keeps the adapter's
            default faithfulness and answer relevancy metrics.
        evaluate_fn: Optional backend callable used by tests to avoid importing
            or calling the real Ragas package.
        settings: Optional runtime settings. When supplied for real evaluation,
            ``evaluation.llm_provider`` and ``evaluation.embedding_provider``
            select project-managed DashScope/DeepSeek clients for Ragas.
    """

    def __init__(
        self,
        *,
        metric_names: Sequence[str] | None = None,
        evaluate_fn: RagasEvaluateFn | None = None,
        settings: RagSettings | None = None,
    ) -> None:
        """Create the underlying adapter while preserving lazy Ragas imports."""

        llm_client = None
        embedding_client = None
        if settings is not None and evaluate_fn is None:
            llm_client = LLMFactory.create(
                settings=settings,
                provider=settings.evaluation.llm_provider,
            )
            embedding_client = EmbeddingFactory.create(
                settings=settings,
                provider=settings.evaluation.embedding_provider,
            )

        runtime_settings = settings.evaluation.ragas if settings is not None else None
        self._adapter = RagasEvaluator(
            metric_names=metric_names,
            evaluate_fn=evaluate_fn,
            llm_client=llm_client,
            embedding_client=embedding_client,
            timeout_seconds=(
                runtime_settings.timeout_seconds if runtime_settings is not None else 300
            ),
            max_workers=runtime_settings.max_workers if runtime_settings is not None else 8,
        )

    def evaluate(
        self,
        dataset: Sequence[Mapping[str, Any]],
        predictions: Sequence[Mapping[str, Any]],
    ) -> dict[str, float]:
        """Evaluate generation quality with Ragas-compatible metrics.

        Args:
            dataset: Golden records with question and reference answer fields.
            predictions: Generated answer records with retrieved text contexts.

        Returns:
            Numeric Ragas scores keyed by metric name.
        """

        return self._adapter.evaluate(dataset, predictions)
