"""Define the splitter component namespace for pure text splitting tools.

Splitter implementations are deliberately limited to ``str -> list[str]`` so
business object adaptation remains in ``DocumentChunker`` rather than this
lower-level utility package. This B7 package boundary is filled with the base
interface and LangChain text-splitter wrapper in B8.
"""

from src.libs.splitter.base_splitter import BaseSplitter
from src.libs.splitter.fake_splitter import FakeSplitter
from src.libs.splitter.markdown_section_splitter import MarkdownSectionSplitter
from src.libs.splitter.recursive_character_splitter import RecursiveCharacterSplitter
from src.libs.splitter.splitter_factory import SplitterFactory

__all__ = (
    "BaseSplitter",
    "FakeSplitter",
    "MarkdownSectionSplitter",
    "RecursiveCharacterSplitter",
    "SplitterFactory",
)
