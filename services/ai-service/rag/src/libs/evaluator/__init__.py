"""Define the evaluator component namespace for RAG quality metrics.

Evaluator implementations run Ragas or custom retrieval/generation metrics
behind a consistent interface so evaluation jobs and Dashboard pages can switch
metric backends through configuration. The namespace exposes fake metrics for
tests and Ragas for generation-quality evaluation.
"""

from src.libs.evaluator.base_evaluator import BaseEvaluator
from src.libs.evaluator.evaluator_factory import EvaluatorFactory
from src.libs.evaluator.fake_evaluator import FakeEvaluator
from src.libs.evaluator.ragas_evaluator import RagasEvaluatorClient

__all__ = (
    "BaseEvaluator",
    "EvaluatorFactory",
    "FakeEvaluator",
    "RagasEvaluatorClient",
)
