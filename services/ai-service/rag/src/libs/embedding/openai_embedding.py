"""Adapt OpenAI dense embeddings to the local embedding contract.

The adapter resolves credentials from settings/environment and uses one SDK
request for each batch. Provider response items are reordered by their explicit
index so callers always receive vectors in input order even when a test double
or future transport returns items out of order. Phase I2 adds a native async SDK
path for online query and evaluation embedding calls.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from openai import AsyncOpenAI, OpenAI

from src.core.errors import ConfigurationError, ProviderError
from src.libs.embedding.base_embedding import BaseEmbedding


class OpenAIEmbedding(BaseEmbedding):
    """Generate dense vectors through an OpenAI-compatible embedding API."""

    def __init__(
        self,
        *,
        model: str,
        dimensions: int,
        api_key_env: str | None = None,
        base_url_env: str | None = None,
        base_url: str | None = None,
        timeout_seconds: int = 60,
        provider_name: str = "openai",
        client: Any | None = None,
        async_client: Any | None = None,
        environ: Mapping[str, str] | None = None,
        **_: Any,
    ) -> None:
        """Configure sync and async embedding clients.

        Args:
            model: Provider-specific embedding model identifier accepted by the
                OpenAI-compatible endpoint.
            dimensions: Required vector length shared with pgvector schema.
            api_key_env: Environment variable containing the OpenAI API key.
            base_url_env: Optional environment variable for a compatible API
                endpoint.
            base_url: Optional literal compatible endpoint.
            timeout_seconds: SDK request timeout.
            provider_name: Configuration-facing provider identifier retained in
                error context when this adapter serves a compatible endpoint.
            client: Optional synchronous SDK-compatible client injected by tests.
            async_client: Optional asynchronous SDK-compatible client used by
                query/evaluation async paths.
            environ: Optional isolated environment mapping for tests.
            **_: Forward-compatible provider settings ignored by this adapter.

        Raises:
            ConfigurationError: If model, dimensions, timeout, or environment
                configuration is invalid.

        Side Effects:
            Creates OpenAI SDK clients when explicit clients are not supplied.
        """

        if not model.strip():
            raise ConfigurationError(
                "OpenAI-compatible embedding model must not be blank"
            )
        if dimensions <= 0:
            raise ConfigurationError(
                "OpenAI-compatible embedding dimensions must be positive"
            )
        if timeout_seconds <= 0:
            raise ConfigurationError(
                "OpenAI-compatible embedding timeout must be positive"
            )
        normalized_provider = provider_name.strip().lower()
        if not normalized_provider:
            raise ConfigurationError("Embedding provider name must not be blank")

        self._model = model
        self._provider = normalized_provider
        self.dimensions = dimensions
        if client is not None:
            self._client = client
            self._async_client = async_client
            return

        environment = os.environ if environ is None else environ
        if not api_key_env or not api_key_env.strip():
            raise ConfigurationError(
                "OpenAI-compatible embedding api_key_env must be configured",
                context={"provider": self._provider, "setting": "api_key_env"},
            )
        api_key = environment.get(api_key_env, "").strip()
        if not api_key:
            raise ConfigurationError(
                f"Missing embedding environment variable: {api_key_env}",
                context={
                    "provider": self._provider,
                    "environment_variable": api_key_env,
                },
            )

        resolved_base_url = base_url
        if base_url_env:
            resolved_base_url = environment.get(base_url_env, "").strip()
            if not resolved_base_url:
                raise ConfigurationError(
                    f"Missing embedding environment variable: {base_url_env}",
                    context={
                        "provider": self._provider,
                        "environment_variable": base_url_env,
                    },
                )

        client_options: dict[str, Any] = {
            "api_key": api_key,
            "timeout": timeout_seconds,
        }
        if resolved_base_url:
            client_options["base_url"] = resolved_base_url
        try:
            self._client = OpenAI(**client_options)
            self._async_client = async_client or AsyncOpenAI(**client_options)
        except Exception as error:
            raise ConfigurationError(
                "Unable to initialize OpenAI-compatible embedding SDK client",
                context={"provider": self._provider},
                cause=error,
            ) from error

    def embed(self, text: str) -> list[float]:
        """Embed one non-blank text through the batch implementation.

        Args:
            text: Text to embed.

        Returns:
            One dense vector with ``self.dimensions`` values.

        Raises:
            ProviderError: If the text is blank or the provider response is
                invalid.
        """

        return self.embed_batch([text])[0]

    async def async_embed(self, text: str) -> list[float]:
        """Embed one text through the native async batch implementation."""

        return (await self.async_embed_batch([text]))[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed one ordered text batch with a single sync SDK request.

        Args:
            texts: Ordered non-blank text strings.

        Returns:
            Dense vectors restored to the original input order.

        Raises:
            ProviderError: If any text is blank, the SDK request fails, response
                indexes are invalid, or vector dimensions do not match config.
        """

        self._validate_texts(texts)
        if not texts:
            return []
        try:
            response = self._client.embeddings.create(
                model=self._model,
                input=list(texts),
                dimensions=self.dimensions,
            )
        except Exception as error:
            raise ProviderError(
                "OpenAI-compatible embedding request failed",
                context={"provider": self._provider, "model": self._model},
                cause=error,
            ) from error
        return self._normalize_vectors(response, expected_count=len(texts))

    async def async_embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed one ordered text batch with the native async SDK request.

        Args:
            texts: Ordered non-blank text strings.

        Returns:
            Dense vectors restored to the original input order.

        Raises:
            ProviderError: If the async client is missing, the request fails, or
                response validation fails.
        """

        self._validate_texts(texts)
        if not texts:
            return []
        if self._async_client is None:
            return await super().async_embed_batch(texts)
        try:
            response = await self._async_client.embeddings.create(
                model=self._model,
                input=list(texts),
                dimensions=self.dimensions,
            )
        except Exception as error:
            raise ProviderError(
                "OpenAI-compatible async embedding request failed",
                context={"provider": self._provider, "model": self._model},
                cause=error,
            ) from error
        return self._normalize_vectors(response, expected_count=len(texts))

    def _validate_texts(self, texts: list[str]) -> None:
        """Validate embedding input before either sync or async transport.

        Args:
            texts: Ordered text strings requested by callers.

        Raises:
            ProviderError: If any supplied text is blank.
        """

        if any(not text.strip() for text in texts):
            raise ProviderError(
                "Cannot embed blank text",
                context={"provider": self._provider, "model": self._model},
            )

    def _normalize_vectors(self, response: Any, *, expected_count: int) -> list[list[float]]:
        """Normalize SDK embedding data into ordered dense vectors.

        Args:
            response: OpenAI-compatible embedding response object.
            expected_count: Number of vectors required by the caller.

        Returns:
            Vectors sorted back into original input order.

        Raises:
            ProviderError: If response count, indexes, or dimensions are invalid.
        """

        data = list(getattr(response, "data", ()) or ())
        if len(data) != expected_count:
            raise ProviderError(
                "OpenAI-compatible embedding response count does not match input",
                context={
                    "provider": self._provider,
                    "model": self._model,
                    "expected_count": expected_count,
                    "actual_count": len(data),
                },
            )

        vectors_by_index: dict[int, list[float]] = {}
        for item in data:
            index = getattr(item, "index", None)
            vector = getattr(item, "embedding", None)
            if not isinstance(index, int) or index < 0 or index >= expected_count:
                raise ProviderError(
                    "OpenAI-compatible embedding response contains an invalid index",
                    context={"provider": self._provider, "model": self._model},
                )
            if not isinstance(vector, list | tuple) or len(vector) != self.dimensions:
                raise ProviderError(
                    "OpenAI-compatible embedding response has unexpected dimensions",
                    context={
                        "provider": self._provider,
                        "model": self._model,
                        "expected_dimensions": self.dimensions,
                    },
                )
            vectors_by_index[index] = [float(value) for value in vector]

        if set(vectors_by_index) != set(range(expected_count)):
            raise ProviderError(
                "OpenAI-compatible embedding response indexes are incomplete",
                context={"provider": self._provider, "model": self._model},
            )
        return [vectors_by_index[index] for index in range(expected_count)]
