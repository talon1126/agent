"""Provide configured metric results for deterministic evaluation tests."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from src.libs.evaluator.base_evaluator import BaseEvaluator


class FakeEvaluator(BaseEvaluator):
    """Return a fixed metric mapping after validating batch alignment."""

    def __init__(self, *, metrics: Mapping[str, float] | None = None) -> None:
        """Configure deterministic evaluation scores.

        Args:
            metrics: Metric names and numeric scores returned by ``evaluate``.
        """

        self._metrics = {
            str(name): float(score) for name, score in dict(metrics or {}).items()
        }

    def evaluate(
        self,
        dataset: Sequence[Mapping[str, Any]],
        predictions: Sequence[Mapping[str, Any]],
    ) -> dict[str, float]:
        """Return configured scores for aligned evaluation inputs.

        Args:
            dataset: Golden evaluation records.
            predictions: Prediction records aligned with ``dataset``.

        Returns:
            A copy of the configured metric mapping.

        Raises:
            ValueError: If dataset and prediction counts differ.
        """

        if len(dataset) != len(predictions):
            raise ValueError("Evaluation dataset and prediction counts must match")
        return dict(self._metrics)
