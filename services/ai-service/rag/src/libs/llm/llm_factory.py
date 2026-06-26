"""Create LLM clients from settings-backed provider names.

The factory uses an explicitly populated registry so provider selection is
centralized and extensible. Built-in implementations are added by
``register_builtin_providers()`` instead of a pre-filled class variable. Business
code calls ``LLMFactory.create(settings=...)`` or passes an explicit provider in
tests; it never branches on OpenAI, Azure, Ollama, or DeepSeek names directly.
"""

from __future__ import annotations

from typing import Any

from src.core.config import RagSettings
from src.core.errors import ConfigurationError
from src.libs.llm.base_llm import BaseLLM
from src.libs.llm.ccswitch_client import CCSwitchClient
from src.libs.llm.deepseek_client import DeepSeekClient
from src.libs.llm.fake_llm import FakeLLM


class LLMFactory:
    """Resolve LLM providers into concrete ``BaseLLM`` instances."""

    _REGISTRY: dict[str, type[BaseLLM]] = {}
    _builtins_registered = False

    @classmethod
    def register_builtin_providers(cls) -> None:
        """Register project-owned LLM implementations once per process.

        Side Effects:
            Populates the registry with the deterministic fake and the
            Bailian-hosted DeepSeek adapter. Additional OpenAI, Azure, and
            Ollama adapters can be added without changing business code.
        """

        if cls._builtins_registered:
            return
        cls.register("fake", FakeLLM)
        cls.register("ccswitch", CCSwitchClient)
        cls.register("deepseek", DeepSeekClient)
        cls._builtins_registered = True

    @classmethod
    def register(cls, provider: str, llm_class: type[BaseLLM]) -> None:
        """Register an LLM implementation under a provider key.

        Args:
            provider: Configuration-facing provider name.
            llm_class: Concrete class implementing ``BaseLLM``.

        Raises:
            ConfigurationError: If the provider name is blank or the class does
                not implement the required interface.
        """

        normalized = provider.strip().lower()
        if not normalized:
            raise ConfigurationError("LLM provider name must not be blank")
        if not issubclass(llm_class, BaseLLM):
            raise ConfigurationError(
                "LLM provider must implement BaseLLM",
                context={"provider": provider, "llm_class": llm_class.__name__},
            )
        cls._REGISTRY[normalized] = llm_class

    @classmethod
    def create(
        cls,
        *,
        settings: RagSettings | None = None,
        provider: str | None = None,
        **override_options: Any,
    ) -> BaseLLM:
        """Create an LLM client from explicit provider or validated settings.

        Args:
            settings: Optional runtime settings. When supplied and ``provider``
                is omitted, ``settings.llm.default`` selects the implementation.
            provider: Optional provider override used by tests and explicit
                dependency injection.
            **override_options: Constructor options overriding provider config.

        Returns:
            A concrete ``BaseLLM`` instance.

        Raises:
            ConfigurationError: If the selected provider is unknown or cannot
                be constructed.
        """

        cls.register_builtin_providers()
        configured_provider = settings.llm.default if settings else ""
        provider_name = (provider or configured_provider).strip().lower()
        if not provider_name:
            raise ConfigurationError("LLM provider must be supplied")

        llm_class = cls._REGISTRY.get(provider_name)
        if llm_class is None:
            raise ConfigurationError(
                "Unsupported LLM provider",
                context={"provider": provider_name, "available": sorted(cls._REGISTRY)},
            )

        options: dict[str, Any] = {}
        if settings is not None:
            provider_settings = settings.llm.providers.get(provider_name)
            if provider_settings is not None:
                options.update(provider_settings.model_dump(exclude_none=True))
        options.update(override_options)

        try:
            return llm_class(**options)
        except TypeError as error:
            raise ConfigurationError(
                "Unable to create LLM provider",
                context={"provider": provider_name, "options": sorted(options)},
                cause=error,
            ) from error

    @classmethod
    def list_providers(cls) -> list[str]:
        """Return registered LLM providers for diagnostics and Dashboard use."""

        cls.register_builtin_providers()
        return sorted(cls._REGISTRY)
