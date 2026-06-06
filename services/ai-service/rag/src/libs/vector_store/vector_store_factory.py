"""Create vector stores through an explicitly populated provider registry.

The B11 registry contains only the in-memory fake implementation. The
production ``pgvector`` adapter is intentionally registered in B12 so selecting
``pgvector`` before that task fails clearly instead of silently writing to an
in-memory substitute.
"""

from __future__ import annotations

from typing import Any

from src.core.config import RagSettings
from src.core.errors import ConfigurationError
from src.libs.vector_store.base_vector_store import BaseVectorStore
from src.libs.vector_store.fake_vector_store import FakeVectorStore


class VectorStoreFactory:
    """Resolve vector-store provider names into ``BaseVectorStore`` instances."""

    _REGISTRY: dict[str, type[BaseVectorStore]] = {}
    _builtins_registered = False

    @classmethod
    def register_builtin_providers(cls) -> None:
        """Register project-owned vector-store implementations once.

        Side Effects:
            Registers the deterministic fake provider. B12 extends this method
            with the real pgvector implementation.
        """

        if cls._builtins_registered:
            return
        cls.register("fake", FakeVectorStore)
        cls._builtins_registered = True

    @classmethod
    def register(
        cls,
        provider: str,
        vector_store_class: type[BaseVectorStore],
    ) -> None:
        """Register a vector-store implementation under a provider key.

        Args:
            provider: Configuration-facing provider name.
            vector_store_class: Concrete ``BaseVectorStore`` subclass.

        Raises:
            ConfigurationError: If the name is blank or the implementation does
                not satisfy the vector-store interface.
        """

        normalized = provider.strip().lower()
        if not normalized:
            raise ConfigurationError("Vector-store provider name must not be blank")
        if not issubclass(vector_store_class, BaseVectorStore):
            raise ConfigurationError(
                "Vector-store provider must implement BaseVectorStore",
                context={
                    "provider": provider,
                    "vector_store_class": vector_store_class.__name__,
                },
            )
        cls._REGISTRY[normalized] = vector_store_class

    @classmethod
    def create(
        cls,
        *,
        settings: RagSettings | None = None,
        provider: str | None = None,
        **override_options: Any,
    ) -> BaseVectorStore:
        """Create a vector store from explicit provider or validated settings.

        Args:
            settings: Optional runtime settings whose
                ``vector_store.provider`` selects the implementation.
            provider: Optional explicit provider override used by tests.
            **override_options: Constructor arguments overriding settings.

        Returns:
            A concrete vector-store implementation.

        Raises:
            ConfigurationError: If no provider is selected, the provider is not
                implemented, or constructor options are invalid.
        """

        cls.register_builtin_providers()
        configured_provider = settings.vector_store.provider if settings else ""
        provider_name = (provider or configured_provider).strip().lower()
        if not provider_name:
            raise ConfigurationError("Vector-store provider must be supplied")

        vector_store_class = cls._REGISTRY.get(provider_name)
        if vector_store_class is None:
            raise ConfigurationError(
                "Unsupported vector-store provider",
                context={"provider": provider_name, "available": sorted(cls._REGISTRY)},
            )

        options: dict[str, Any] = {}
        if settings is not None and provider is None:
            options.update(
                settings.vector_store.model_dump(
                    exclude={"provider"},
                    exclude_none=True,
                )
            )
        options.update(override_options)

        try:
            return vector_store_class(**options)
        except TypeError as error:
            raise ConfigurationError(
                "Unable to create vector-store provider",
                context={"provider": provider_name, "options": sorted(options)},
                cause=error,
            ) from error

    @classmethod
    def list_providers(cls) -> list[str]:
        """Return registered vector-store providers for diagnostics."""

        cls.register_builtin_providers()
        return sorted(cls._REGISTRY)
