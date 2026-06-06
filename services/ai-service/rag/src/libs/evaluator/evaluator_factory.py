"""Create evaluation backends through an explicit provider registry.

B11 registers only the deterministic fake evaluator. Custom retrieval metrics
and Ragas are implemented and registered in Phase G, so selecting those names
early produces a clear configuration error instead of fabricated scores.
"""

from __future__ import annotations

from typing import Any

from src.core.config import RagSettings
from src.core.errors import ConfigurationError
from src.libs.evaluator.base_evaluator import BaseEvaluator
from src.libs.evaluator.fake_evaluator import FakeEvaluator


class EvaluatorFactory:
    """Resolve evaluator provider names into ``BaseEvaluator`` instances."""

    _REGISTRY: dict[str, type[BaseEvaluator]] = {}
    _builtins_registered = False

    @classmethod
    def register_builtin_providers(cls) -> None:
        """Register project-owned evaluator implementations once.

        Side Effects:
            Registers the deterministic fake provider used by unit tests.
        """

        if cls._builtins_registered:
            return
        cls.register("fake", FakeEvaluator)
        cls._builtins_registered = True

    @classmethod
    def register(
        cls,
        provider: str,
        evaluator_class: type[BaseEvaluator],
    ) -> None:
        """Register an evaluator implementation under a provider key.

        Args:
            provider: Configuration-facing evaluator name.
            evaluator_class: Concrete ``BaseEvaluator`` subclass.

        Raises:
            ConfigurationError: If the provider is blank or the class violates
                the evaluator interface.
        """

        normalized = provider.strip().lower()
        if not normalized:
            raise ConfigurationError("Evaluator provider name must not be blank")
        if not issubclass(evaluator_class, BaseEvaluator):
            raise ConfigurationError(
                "Evaluator provider must implement BaseEvaluator",
                context={
                    "provider": provider,
                    "evaluator_class": evaluator_class.__name__,
                },
            )
        cls._REGISTRY[normalized] = evaluator_class

    @classmethod
    def create(
        cls,
        *,
        settings: RagSettings | None = None,
        provider: str | None = None,
        **override_options: Any,
    ) -> BaseEvaluator:
        """Create an evaluator from an explicit provider.

        Args:
            settings: Reserved for signature consistency. Current evaluation
                settings define datasets and metrics but no provider selector.
            provider: Explicit evaluator provider such as ``fake``.
            **override_options: Constructor arguments for the evaluator.

        Returns:
            A concrete evaluator implementation.

        Raises:
            ConfigurationError: If no provider is supplied, the provider is
                unavailable, or constructor options are invalid.
        """

        cls.register_builtin_providers()
        del settings
        provider_name = (provider or "").strip().lower()
        if not provider_name:
            raise ConfigurationError("Evaluator provider must be supplied")

        evaluator_class = cls._REGISTRY.get(provider_name)
        if evaluator_class is None:
            raise ConfigurationError(
                "Unsupported evaluator provider",
                context={"provider": provider_name, "available": sorted(cls._REGISTRY)},
            )

        try:
            return evaluator_class(**override_options)
        except TypeError as error:
            raise ConfigurationError(
                "Unable to create evaluator provider",
                context={"provider": provider_name, "options": sorted(override_options)},
                cause=error,
            ) from error

    @classmethod
    def list_providers(cls) -> list[str]:
        """Return registered evaluator providers for diagnostics."""

        cls.register_builtin_providers()
        return sorted(cls._REGISTRY)
