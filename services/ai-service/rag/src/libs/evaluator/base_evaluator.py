"""Define the provider-independent RAG evaluation contract.

Evaluation jobs and the Streamlit Dashboard call this interface without
depending on Ragas or custom metric implementations. Concrete evaluators own
dataset validation and metric calculation while returning a stable
``metric_name -> score`` mapping.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import Any


class BaseEvaluator(ABC):
    """Specify the minimal batch evaluation interface."""

    @abstractmethod
    def evaluate(
        self,
        dataset: Sequence[Mapping[str, Any]],
        predictions: Sequence[Mapping[str, Any]],
    ) -> dict[str, float]:
        """Evaluate predictions against one dataset.

        Args:
            dataset: Golden examples containing questions, answers, and source
                references required by the selected metrics.
            predictions: Generated answers or retrieved chunk IDs aligned with
                the dataset.

        Returns:
            Numeric metric scores keyed by stable metric names.

        Raises:
            ValueError: If input lengths or records are invalid.
            ProviderError: Concrete evaluators may raise this when an external
                evaluation backend fails.
        """
