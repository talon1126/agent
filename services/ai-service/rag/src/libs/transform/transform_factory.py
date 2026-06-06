"""Create chunk transform implementations from explicit provider names.

TransformFactory follows the shared pluggable-component pattern: the registry is
empty at import time, built-ins are injected by ``register_builtin_providers()``,
and public creation methods automatically ensure built-ins are available before
looking up providers. Transform orchestration will later read the configured
transform step chain from settings and call this factory with each step's
provider.
"""

from __future__ import annotations

from typing import Any

from src.core.config import RagSettings
from src.core.errors import ConfigurationError
from src.libs.transform.base_transform import BaseTransform
from src.libs.transform.fake_transform import FakeTransform


class TransformFactory:
    """Resolve transform providers into concrete ``BaseTransform`` instances."""

    _REGISTRY: dict[str, type[BaseTransform]] = {}
    _builtins_registered = False

    @classmethod
    def register_builtin_providers(cls) -> None:
        """Register project-owned transform implementations once per process.

        Side Effects:
            Populates the registry with the fake transform used by tests. Real
            metadata, rewrite, merge, denoise, and image-to-text transforms are
            added in later tasks without changing pipeline code.
        """

        if cls._builtins_registered:
            return
        cls.register("fake", FakeTransform)
        cls._builtins_registered = True

    @classmethod
    def register(cls, provider: str, transform_class: type[BaseTransform]) -> None:
        """Register one transform implementation under a provider key.

        Args:
            provider: Configuration-facing transform provider name.
            transform_class: Concrete class implementing ``BaseTransform``.

        Raises:
            ConfigurationError: If the provider name is blank or the class does
                not satisfy the transform interface.
        """

        normalized = provider.strip().lower()
        if not normalized:
            raise ConfigurationError("Transform provider name must not be blank")
        if not issubclass(transform_class, BaseTransform):
            raise ConfigurationError(
                "Transform provider must implement BaseTransform",
                context={
                    "provider": provider,
                    "transform_class": transform_class.__name__,
                },
            )
        cls._REGISTRY[normalized] = transform_class

    @classmethod
    def create(
        cls,
        *,
        settings: RagSettings | None = None,
        provider: str | None = None,
        **override_options: Any,
    ) -> BaseTransform:
        """Create a transform from an explicit provider name.

        Args:
            settings: Reserved for signature consistency with other factories.
                B10 has no global transform provider selector in
                ``settings.yaml`` yet, so orchestration must pass ``provider``.
            provider: Provider key such as ``fake``.
            **override_options: Constructor options passed to the transform.

        Returns:
            A concrete ``BaseTransform`` instance.

        Raises:
            ConfigurationError: If no provider is supplied, the provider is
                unknown, or construction options are invalid.
        """

        cls.register_builtin_providers()
        del settings
        provider_name = (provider or "").strip().lower()
        if not provider_name:
            raise ConfigurationError("Transform provider must be supplied")

        transform_class = cls._REGISTRY.get(provider_name)
        if transform_class is None:
            raise ConfigurationError(
                "Unsupported transform provider",
                context={"provider": provider_name, "available": sorted(cls._REGISTRY)},
            )

        try:
            return transform_class(**override_options)
        except TypeError as error:
            raise ConfigurationError(
                "Unable to create transform provider",
                context={"provider": provider_name, "options": sorted(override_options)},
                cause=error,
            ) from error

    @classmethod
    def list_providers(cls) -> list[str]:
        """Return registered transform providers for diagnostics and Dashboard use."""

        cls.register_builtin_providers()
        return sorted(cls._REGISTRY)
