"""Evaluation helpers for offline RAG quality measurement.

This package contains deterministic metric implementations and later runner
adapters used by Phase G. The modules are placed under ``observability`` because
their primary consumer is the local Dashboard evaluation panel and quality
tracking workflow, while the actual retrieval pipeline remains under
``src.core``.
"""

from src.observability.evaluation.metrics import HitRateMetric, MRRMetric, NDCGMetric

__all__ = ["HitRateMetric", "MRRMetric", "NDCGMetric"]
