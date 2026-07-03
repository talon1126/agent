"""Create loader implementations from provider names or source paths.

The factory centralizes provider lookup so ingestion code never needs to repeat
suffix checks or instantiate concrete loader classes directly. The registry
starts empty and is populated through ``register_builtin_providers()`` so the
list of built-in implementations stays in one explicit method instead of being
hidden in class-variable initialization.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.core.errors import ConfigurationError
from src.libs.loader.base_loader import BaseLoader
from src.libs.loader.fake_loader import FakeLoader
from src.libs.loader.markdown_loader import MarkdownLoader
from src.libs.loader.pdf_loader import PdfLoader


class LoaderFactory:
    """Resolve loader providers into concrete ``BaseLoader`` instances."""

    _REGISTRY: dict[str, type[BaseLoader]] = {}
    _builtins_registered = False
    _SOURCE_SUFFIXES: dict[str, str] = {
        ".md": "markdown",
        ".markdown": "markdown",
        ".pdf": "pdf",
    }

    @classmethod
    def register_builtin_providers(cls) -> None:
        """Register project-owned loader implementations once per process.

        Side Effects:
            Populates the loader registry with fake, Markdown, and PDF loaders.
            The method is idempotent so factories and diagnostics can call it
            freely without duplicating registrations.
        """

        if cls._builtins_registered:
            return
        cls.register("fake", FakeLoader)
        cls.register("markdown", MarkdownLoader)
        cls.register("pdf", PdfLoader)
        cls._builtins_registered = True

    @classmethod
    def register(cls, provider: str, loader_class: type[BaseLoader]) -> None:
        """Register a loader implementation for later factory creation.

        Args:
            provider: Configuration-facing provider name.
            loader_class: Concrete class implementing ``BaseLoader``.

        Raises:
            ConfigurationError: If the provider name is blank or the class does
                not implement the loader interface.
        """

        normalized = provider.strip().lower()
        if not normalized:
            raise ConfigurationError("Loader provider name must not be blank")
        if not issubclass(loader_class, BaseLoader):
            raise ConfigurationError(
                "Loader provider must implement BaseLoader",
                context={"provider": provider, "loader_class": loader_class.__name__},
            )
        cls._REGISTRY[normalized] = loader_class

    @classmethod
    def create(cls, provider: str, **kwargs: Any) -> BaseLoader:
        """Create one loader by provider name using the registry dictionary.

        Args:
            provider: Registered loader provider name such as ``markdown``.
            **kwargs: Constructor options forwarded to the selected loader.

        Returns:
            A concrete ``BaseLoader`` instance.

        Raises:
            ConfigurationError: If the provider is unknown or construction
                fails.
        """

        cls.register_builtin_providers()
        provider_name = provider.strip().lower()
        loader_class = cls._REGISTRY.get(provider_name)
        if loader_class is None:
            raise ConfigurationError(
                "Unsupported loader provider",
                context={"provider": provider, "available": sorted(cls._REGISTRY)},
            )
        try:
            return loader_class(**kwargs)
        except TypeError as error:
            raise ConfigurationError(
                "Unable to create loader provider",
                context={"provider": provider_name},
                cause=error,
            ) from error

    @classmethod
    def for_source(cls, source: str | Path, **kwargs: Any) -> BaseLoader:
        """Create a loader by mapping the source suffix to a provider.

        Args:
            source: Filesystem path whose suffix selects a loader.
            **kwargs: Constructor options forwarded to the selected loader.

        Returns:
            A concrete ``BaseLoader`` instance for the source type.

        Raises:
            ConfigurationError: If no loader is registered for the source
                suffix.
        """

        suffix = Path(source).suffix.lower()
        provider = cls._SOURCE_SUFFIXES.get(suffix)
        if provider is None:
            raise ConfigurationError(
                "Unsupported loader source suffix",
                context={
                    "source": str(source),
                    "suffix": suffix,
                    "available_suffixes": sorted(cls._SOURCE_SUFFIXES),
                },
            )
        return cls.create(provider, **kwargs)

    @classmethod
    def list_providers(cls) -> list[str]:
        """Return registered loader providers for diagnostics and Dashboard use."""

        cls.register_builtin_providers()
        return sorted(cls._REGISTRY)
