"""Define the evaluator component namespace for RAG quality metrics.

Evaluator implementations will run Ragas or custom retrieval/generation
metrics behind a consistent interface so evaluation jobs and Dashboard pages can
switch metric backends through configuration. B7 creates only the package
boundary; B11 adds the base evaluator, factory, and fake/custom implementations.
"""

__all__: tuple[str, ...] = ()
