"""Define the embedding component namespace for dense vector clients.

Embedding implementations will provide single-text and batch vector generation
behind a common interface so ingestion code can switch providers through
configuration. This package is an empty B7 boundary until B9 introduces the
base interface, factory, OpenAI implementation, and fake implementation.
"""

__all__: tuple[str, ...] = ()
