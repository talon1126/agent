"""Create rerankers with explicit registration and configured fallback.

The factory selects configured rerankers without exposing provider branches to
query orchestration. When a settings-selected implementation is not registered,
it may use ``settings.rerank.fallback``. Explicit unknown provider overrides
still fail fast unless the caller explicitly supplies a fallback provider.
"""

from __future__ import annotations

from typing import Any

from src.core.config import RagSettings
from src.core.errors import ConfigurationError
from src.libs.reranker.base_reranker import BaseReranker
from src.libs.reranker.fake_reranker import FakeReranker
from src.libs.reranker.no_op_reranker import NoOpReranker


class RerankerFactory:
    """Resolve reranker providers and safe fallback strategies."""

    _REGISTRY: dict[str, type[BaseReranker]] = {}
    _builtins_registered = False

    @classmethod
    def register_builtin_providers(cls) -> None:
        """Register deterministic and safe fallback implementations once.

        Side Effects:
            Registers ``fake`` for tests and aliases ``none``, ``rrf``, and
            ``fallback`` to the order-preserving no-op implementation.
        """

        if cls._builtins_registered:
            return
        cls.register("fake", FakeReranker)
        cls.register("none", NoOpReranker)
        cls.register("rrf", NoOpReranker)
        cls.register("fallback", NoOpReranker)
        cls._builtins_registered = True

    @classmethod
    def register(
        cls,
        provider: str,
        reranker_class: type[BaseReranker],
    ) -> None:
        """Register a reranker implementation under a provider key.

        Args:
            provider: Configuration-facing provider or strategy name.
            reranker_class: Concrete ``BaseReranker`` subclass.

        Raises:
            ConfigurationError: If the provider is blank or the class violates
                the reranker interface.
        """

        normalized = provider.strip().lower()
        if not normalized:
            raise ConfigurationError("Reranker provider name must not be blank")
        if not issubclass(reranker_class, BaseReranker):
            raise ConfigurationError(
                "Reranker provider must implement BaseReranker",
                context={
                    "provider": provider,
                    "reranker_class": reranker_class.__name__,
                },
            )
        cls._REGISTRY[normalized] = reranker_class

    @classmethod
    def create(
        cls,
        *,
        settings: RagSettings | None = None,
        provider: str | None = None,
        fallback_provider: str | None = None,
        **override_options: Any,
    ) -> BaseReranker:
        """Create a reranker and apply configured fallback when appropriate.

        Args:
            settings: Optional runtime settings selecting the default and
                fallback providers.
            provider: Optional explicit provider override.
            fallback_provider: Optional explicit fallback used only when the
                selected provider is unavailable.
            **override_options: Constructor options passed to the selected
                implementation.

        Returns:
            A concrete reranker or order-preserving fallback implementation.

        Raises:
            ConfigurationError: If no provider is selected, neither selected
                nor fallback providers are registered, or construction fails.
        """

        cls.register_builtin_providers()
        explicit_provider = provider is not None
        if provider is not None:
            provider_name = provider.strip().lower()
        elif settings is not None and not settings.rerank.enabled:
            provider_name = "none"
        elif settings is not None:
            provider_name = settings.rerank.default.strip().lower()
        else:
            provider_name = ""

        if not provider_name:
            raise ConfigurationError("Reranker provider must be supplied")

        selected_name = provider_name
        reranker_class = cls._REGISTRY.get(selected_name)
        configured_fallback = fallback_provider
        if configured_fallback is None and settings is not None and not explicit_provider:
            configured_fallback = settings.rerank.fallback

        if reranker_class is None and configured_fallback:
            fallback_name = configured_fallback.strip().lower()
            reranker_class = cls._REGISTRY.get(fallback_name)
            if reranker_class is not None:
                selected_name = fallback_name

        if reranker_class is None:
            raise ConfigurationError(
                "Unsupported reranker provider",
                context={
                    "provider": provider_name,
                    "fallback": configured_fallback,
                    "available": sorted(cls._REGISTRY),
                },
            )

        options: dict[str, Any] = {}
        if settings is not None and selected_name in settings.rerank.providers:
            options.update(
                settings.rerank.providers[selected_name].model_dump(exclude_none=True)
            )
        options.update(override_options)

        try:
            return reranker_class(**options)
        except TypeError as error:
            raise ConfigurationError(
                "Unable to create reranker provider",
                context={"provider": selected_name, "options": sorted(options)},
                cause=error,
            ) from error

    @classmethod
    def list_providers(cls) -> list[str]:
        """Return registered reranker providers and fallback strategies."""

        cls.register_builtin_providers()
        return sorted(cls._REGISTRY)
