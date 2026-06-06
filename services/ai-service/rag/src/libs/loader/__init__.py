"""Define the loader component namespace for source-to-Document adapters.

Loader implementations will convert supported source formats into validated
``Document`` objects after ingestion-level deduplication decides a source should
be processed. This package intentionally contains only the namespace boundary in
B7; B8 adds the base interface, factory, and concrete Markdown/PDF loaders.
"""

__all__: tuple[str, ...] = ()
