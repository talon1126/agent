"""Evaluation helpers for offline RAG quality measurement.

This package contains deterministic metric implementations and later runner
adapters used by Phase G. The modules are placed under ``observability`` because
their primary consumer is the local Dashboard evaluation panel and quality
tracking workflow, while the actual retrieval pipeline remains under
``src.core``.
"""

from src.observability.evaluation.metrics import HitRateMetric, MRRMetric, NDCGMetric
from src.observability.evaluation.ragas_adapter import RagasEvaluator
from src.observability.evaluation.runner import (
    DEFAULT_RETRIEVAL_STRATEGIES,
    EvaluationRunner,
    RetrievalStrategy,
    StrategyComparisonResult,
    StrategyRetrievalFn,
)

__all__ = [
    "DEFAULT_RETRIEVAL_STRATEGIES",
    "EvaluationRunner",
    "HitRateMetric",
    "MRRMetric",
    "NDCGMetric",
    "RagasEvaluator",
    "RetrievalStrategy",
    "StrategyComparisonResult",
    "StrategyRetrievalFn",
]
