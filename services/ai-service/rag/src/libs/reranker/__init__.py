"""Define the reranker component namespace for candidate reordering adapters.

Reranker implementations will score and reorder retrieval candidates using
Cross-Encoder, LLM, or fallback strategies without changing hybrid retrieval
code. This B7 namespace receives concrete contracts and implementations in B11
and later reranker tasks.
"""

__all__: tuple[str, ...] = ()
