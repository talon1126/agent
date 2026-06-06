"""Define the evaluator component namespace for RAG quality metrics.

Evaluator implementations run Ragas or custom retrieval/generation metrics
behind a consistent interface so evaluation jobs and Dashboard pages can switch
metric backends through configuration. B11 provides the base evaluator, factory,
and fake; real metric implementations are added in Phase G.
"""

from src.libs.evaluator.base_evaluator import BaseEvaluator
from src.libs.evaluator.evaluator_factory import EvaluatorFactory
from src.libs.evaluator.fake_evaluator import FakeEvaluator

__all__ = (
    "BaseEvaluator",
    "EvaluatorFactory",
    "FakeEvaluator",
)
