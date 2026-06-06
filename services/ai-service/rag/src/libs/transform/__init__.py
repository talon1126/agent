"""Define the transform component namespace for chunk enhancement stages.

Transform implementations will enrich, rewrite, merge, denoise, and attach
image captions to chunk-like data after raw splitting. B7 creates the namespace
only; B10 adds the minimal transform interface, factory, and fake test
implementation.
"""

__all__: tuple[str, ...] = ()
