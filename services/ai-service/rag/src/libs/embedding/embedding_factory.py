"""Create embedding clients from settings-backed provider names.

The registry starts empty and is populated by
``register_builtin_providers()``. This keeps built-in provider injection
explicit while preserving the same ``register()`` extension point for later
OpenAI or local embedding adapters.
"""

from __future__ import annotations

from typing import Any

from src.core.config import RagSettings
from src.core.errors import ConfigurationError
from src.libs.embedding.base_embedding import BaseEmbedding
from src.libs.embedding.fake_embedding import FakeEmbedding


class EmbeddingFactory:
    """Resolve embedding providers through a registry-backed factory."""

    _REGISTRY: dict[str, type[BaseEmbedding]] = {}
    _builtins_registered = False

    @classmethod
    def register_builtin_providers(cls) -> None:
        """Register project-owned embedding implementations once per process.

        Side Effects:
            Populates the embedding registry with the deterministic fake
            provider used by tests. Real OpenAI embedding support is registered
            in a later task.
        """

        if cls._builtins_registered:
            return
        cls.register("fake", FakeEmbedding)
        cls._builtins_registered = True

    @classmethod
    def register(cls, provider: str, embedding_class: type[BaseEmbedding]) -> None:
        """Register an embedding implementation under a provider key.

        Args:
            provider: Configuration-facing provider name.
            embedding_class: Concrete class implementing ``BaseEmbedding``.

        Raises:
            ConfigurationError: If the provider name is blank or the class does
                not implement the embedding interface.
        """

        normalized = provider.strip().lower()
        if not normalized:
            raise ConfigurationError("Embedding provider name must not be blank")
        if not issubclass(embedding_class, BaseEmbedding):
            raise ConfigurationError(
                "Embedding provider must implement BaseEmbedding",
                context={
                    "provider": provider,
                    "embedding_class": embedding_class.__name__,
                },
            )
        cls._REGISTRY[normalized] = embedding_class

    @classmethod
    def create(
        cls,
        *,
        settings: RagSettings | None = None,
        provider: str | None = None,
        **override_options: Any,
    ) -> BaseEmbedding:
        """Create an embedding client from explicit provider or settings.

        Args:
            settings: Optional runtime settings. When supplied and ``provider``
                is omitted, ``settings.embedding.default`` selects the
                implementation and its provider options.
            provider: Optional provider override, primarily used by tests.
            **override_options: Constructor options overriding provider config.

        Returns:
            A concrete ``BaseEmbedding`` instance.

        Raises:
            ConfigurationError: If the selected provider is unknown or cannot
                be constructed.
        """

        cls.register_builtin_providers()
        configured_provider = settings.embedding.default if settings else ""
        provider_name = (provider or configured_provider).strip().lower()
        if not provider_name:
            raise ConfigurationError("Embedding provider must be supplied")

        embedding_class = cls._REGISTRY.get(provider_name)
        if embedding_class is None:
            raise ConfigurationError(
                "Unsupported embedding provider",
                context={"provider": provider_name, "available": sorted(cls._REGISTRY)},
            )

        options: dict[str, Any] = {}
        if settings is not None:
            provider_settings = settings.embedding.providers.get(provider_name)
            if provider_settings is not None:
                options.update(provider_settings.model_dump(exclude_none=True))
        options.update(override_options)

        try:
            return embedding_class(**options)
        except TypeError as error:
            raise ConfigurationError(
                "Unable to create embedding provider",
                context={"provider": provider_name, "options": sorted(options)},
                cause=error,
            ) from error

    @classmethod
    def list_providers(cls) -> list[str]:
        """Return registered embedding providers for diagnostics and Dashboard use."""

        cls.register_builtin_providers()
        return sorted(cls._REGISTRY)
