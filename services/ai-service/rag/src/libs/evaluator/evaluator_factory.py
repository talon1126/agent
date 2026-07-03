"""Create evaluation backends through an explicit provider registry.

The factory is the single creation boundary for Dashboard services and
evaluation scripts. It registers deterministic fake metrics for unit tests and
Ragas for real generation-quality evaluation while keeping Ragas itself lazy
inside the concrete provider.
"""

from __future__ import annotations

from typing import Any

from src.core.config import RagSettings
from src.core.errors import ConfigurationError
from src.libs.evaluator.base_evaluator import BaseEvaluator
from src.libs.evaluator.fake_evaluator import FakeEvaluator
from src.libs.evaluator.ragas_evaluator import RagasEvaluatorClient


class EvaluatorFactory:
    """Resolve evaluator provider names into ``BaseEvaluator`` instances."""

    _REGISTRY: dict[str, type[BaseEvaluator]] = {}
    _builtins_registered = False

    @classmethod
    def register_builtin_providers(cls) -> None:
        """Register project-owned evaluator implementations once.

        Side Effects:
            Registers the deterministic fake provider used by unit tests and
            the Ragas provider used by Phase G generation-quality evaluation.
        """

        if cls._builtins_registered:
            return
        cls.register("fake", FakeEvaluator)
        cls.register("ragas", RagasEvaluatorClient)
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
            settings: Optional runtime settings passed to evaluators that need
                configured model providers, such as Ragas.
            provider: Explicit evaluator provider such as ``fake``.
            **override_options: Constructor arguments for the evaluator.

        Returns:
            A concrete evaluator implementation.

        Raises:
            ConfigurationError: If no provider is supplied, the provider is
                unavailable, or constructor options are invalid.
        """

        cls.register_builtin_providers()
        provider_name = (provider or "").strip().lower()
        if not provider_name:
            raise ConfigurationError("Evaluator provider must be supplied")

        evaluator_class = cls._REGISTRY.get(provider_name)
        if evaluator_class is None:
            raise ConfigurationError(
                "Unsupported evaluator provider",
                context={"provider": provider_name, "available": sorted(cls._REGISTRY)},
            )

        options = dict(override_options)
        if settings is not None and evaluator_class is RagasEvaluatorClient:
            options.setdefault("settings", settings)

        try:
            return evaluator_class(**options)
        except TypeError as error:
            raise ConfigurationError(
                "Unable to create evaluator provider",
                context={"provider": provider_name, "options": sorted(options)},
                cause=error,
            ) from error

    @classmethod
    def list_providers(cls) -> list[str]:
        """Return registered evaluator providers for diagnostics."""

        cls.register_builtin_providers()
        return sorted(cls._REGISTRY)
