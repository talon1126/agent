"""Create text splitter implementations from settings-backed provider names.

The registry is populated through ``register_builtin_providers()`` rather than a
pre-filled class variable. This keeps the built-in splitter list explicit,
idempotent, and aligned with the shared Factory pattern used by the other
pluggable components.
"""

from __future__ import annotations

from typing import Any

from src.core.config import RagSettings
from src.core.errors import ConfigurationError
from src.libs.splitter.base_splitter import BaseSplitter
from src.libs.splitter.fake_splitter import FakeSplitter
from src.libs.splitter.markdown_section_splitter import MarkdownSectionSplitter
from src.libs.splitter.recursive_character_splitter import RecursiveCharacterSplitter


class SplitterFactory:
    """Resolve splitter providers through a registry-backed factory."""

    _REGISTRY: dict[str, type[BaseSplitter]] = {}
    _builtins_registered = False

    @classmethod
    def register_builtin_providers(cls) -> None:
        """Register project-owned splitter implementations once per process.

        Side Effects:
            Populates the splitter registry with the deterministic fake splitter,
            the recursive character splitter, and the Markdown section splitter.
            The method is idempotent so
            ``create()`` and ``list_providers()`` can safely call it.
        """

        if cls._builtins_registered:
            return
        cls.register("fake", FakeSplitter)
        cls.register("recursive_character", RecursiveCharacterSplitter)
        cls.register("markdown_section", MarkdownSectionSplitter)
        cls._builtins_registered = True

    @classmethod
    def register(cls, provider: str, splitter_class: type[BaseSplitter]) -> None:
        """Register a splitter implementation for future configuration use.

        Args:
            provider: Configuration-facing splitter provider name.
            splitter_class: Concrete class implementing ``BaseSplitter``.

        Raises:
            ConfigurationError: If the provider name is blank or the class does
                not satisfy the splitter interface.
        """

        normalized = provider.strip().lower()
        if not normalized:
            raise ConfigurationError("Splitter provider name must not be blank")
        if not issubclass(splitter_class, BaseSplitter):
            raise ConfigurationError(
                "Splitter provider must implement BaseSplitter",
                context={"provider": provider, "splitter_class": splitter_class.__name__},
            )
        cls._REGISTRY[normalized] = splitter_class

    @classmethod
    def create(
        cls,
        *,
        settings: RagSettings | None = None,
        provider: str | None = None,
        **override_options: Any,
    ) -> BaseSplitter:
        """Create a splitter from explicit provider or ``settings.yaml``.

        Args:
            settings: Optional validated runtime settings. When supplied and
                ``provider`` is omitted, ``settings.splitter.default`` selects
                the implementation and its provider options.
            provider: Optional provider override, primarily used by tests.
            **override_options: Options that override settings-derived values
                before constructing the splitter.

        Returns:
            A concrete ``BaseSplitter`` instance.

        Raises:
            ConfigurationError: If no provider can be resolved, the provider is
                unknown, or construction arguments are invalid.
        """

        cls.register_builtin_providers()
        configured_provider = settings.splitter.default if settings else ""
        provider_name = (provider or configured_provider).strip().lower()
        if not provider_name:
            raise ConfigurationError("Splitter provider must be supplied")

        splitter_class = cls._REGISTRY.get(provider_name)
        if splitter_class is None:
            raise ConfigurationError(
                "Unsupported splitter provider",
                context={"provider": provider_name, "available": sorted(cls._REGISTRY)},
            )

        options: dict[str, Any] = {}
        if settings is not None:
            options.update(settings.splitter.providers.get(provider_name, {}))
        options.update(override_options)

        try:
            return splitter_class(**options)
        except TypeError as error:
            raise ConfigurationError(
                "Unable to create splitter provider",
                context={"provider": provider_name, "options": sorted(options)},
                cause=error,
            ) from error

    @classmethod
    def list_providers(cls) -> list[str]:
        """Return registered splitter providers for diagnostics and Dashboard use."""

        cls.register_builtin_providers()
        return sorted(cls._REGISTRY)
