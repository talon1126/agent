"""Define the splitter component namespace for pure text splitting tools.

Splitter implementations are deliberately limited to ``str -> list[str]`` so
business object adaptation remains in ``DocumentChunker`` rather than this
lower-level utility package. This B7 package boundary is filled with the base
interface and LangChain text-splitter wrapper in B8.
"""

__all__: tuple[str, ...] = ()
