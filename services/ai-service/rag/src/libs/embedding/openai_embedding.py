"""Adapt OpenAI dense embeddings to the local embedding contract.

The adapter resolves the API key from the environment-variable name stored in
``settings.yaml`` and uses one SDK request for each batch. Provider response
items are reordered by their explicit index so callers always receive vectors
in input order even when a test double or future transport returns items out of
order.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from openai import OpenAI

from src.core.errors import ConfigurationError, ProviderError
from src.libs.embedding.base_embedding import BaseEmbedding


class OpenAIEmbedding(BaseEmbedding):
    """Generate dense vectors with the configured OpenAI embedding model."""

    def __init__(
        self,
        *,
        model: str,
        dimensions: int,
        api_key_env: str | None = None,
        base_url_env: str | None = None,
        base_url: str | None = None,
        timeout_seconds: int = 60,
        client: Any | None = None,
        environ: Mapping[str, str] | None = None,
        **_: Any,
    ) -> None:
        """Configure the embedding provider and expected vector dimensions.

        Args:
            model: OpenAI embedding model identifier.
            dimensions: Required vector length shared with pgvector schema.
            api_key_env: Environment variable containing the OpenAI API key.
            base_url_env: Optional environment variable for a compatible API
                endpoint.
            base_url: Optional literal compatible endpoint.
            timeout_seconds: SDK request timeout.
            client: Optional SDK-compatible client injected by tests.
            environ: Optional isolated environment mapping for tests.
            **_: Forward-compatible provider settings ignored by this adapter.

        Raises:
            ConfigurationError: If model, dimensions, timeout, or environment
                configuration is invalid.

        Side Effects:
            Creates an OpenAI SDK client when ``client`` is not supplied.
        """

        if not model.strip():
            raise ConfigurationError("OpenAI embedding model must not be blank")
        if dimensions <= 0:
            raise ConfigurationError("OpenAI embedding dimensions must be positive")
        if timeout_seconds <= 0:
            raise ConfigurationError("OpenAI embedding timeout must be positive")

        self._model = model
        self.dimensions = dimensions
        if client is not None:
            self._client = client
            return

        environment = os.environ if environ is None else environ
        if not api_key_env or not api_key_env.strip():
            raise ConfigurationError(
                "OpenAI embedding api_key_env must be configured",
                context={"provider": "openai", "setting": "api_key_env"},
            )
        api_key = environment.get(api_key_env, "").strip()
        if not api_key:
            raise ConfigurationError(
                f"Missing OpenAI environment variable: {api_key_env}",
                context={
                    "provider": "openai",
                    "environment_variable": api_key_env,
                },
            )

        resolved_base_url = base_url
        if base_url_env:
            resolved_base_url = environment.get(base_url_env, "").strip()
            if not resolved_base_url:
                raise ConfigurationError(
                    f"Missing OpenAI environment variable: {base_url_env}",
                    context={
                        "provider": "openai",
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
        except Exception as error:
            raise ConfigurationError(
                "Unable to initialize OpenAI embedding SDK client",
                context={"provider": "openai"},
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

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed one ordered text batch with a single SDK request.

        Args:
            texts: Ordered non-blank text strings.

        Returns:
            Dense vectors restored to the original input order.

        Raises:
            ProviderError: If any text is blank, the SDK request fails, response
                indexes are invalid, or vector dimensions do not match config.
        """

        if not texts:
            return []
        if any(not text.strip() for text in texts):
            raise ProviderError(
                "Cannot embed blank text",
                context={"provider": "openai", "model": self._model},
            )

        try:
            response = self._client.embeddings.create(
                model=self._model,
                input=list(texts),
                dimensions=self.dimensions,
            )
        except Exception as error:
            raise ProviderError(
                "OpenAI embedding request failed",
                context={"provider": "openai", "model": self._model},
                cause=error,
            ) from error

        data = list(getattr(response, "data", ()) or ())
        if len(data) != len(texts):
            raise ProviderError(
                "OpenAI embedding response count does not match input",
                context={
                    "provider": "openai",
                    "model": self._model,
                    "expected_count": len(texts),
                    "actual_count": len(data),
                },
            )

        vectors_by_index: dict[int, list[float]] = {}
        for item in data:
            index = getattr(item, "index", None)
            vector = getattr(item, "embedding", None)
            if not isinstance(index, int) or index < 0 or index >= len(texts):
                raise ProviderError(
                    "OpenAI embedding response contains an invalid index",
                    context={"provider": "openai", "model": self._model},
                )
            if not isinstance(vector, list | tuple) or len(vector) != self.dimensions:
                raise ProviderError(
                    "OpenAI embedding response has unexpected dimensions",
                    context={
                        "provider": "openai",
                        "model": self._model,
                        "expected_dimensions": self.dimensions,
                    },
                )
            vectors_by_index[index] = [float(value) for value in vector]

        if set(vectors_by_index) != set(range(len(texts))):
            raise ProviderError(
                "OpenAI embedding response indexes are incomplete",
                context={"provider": "openai", "model": self._model},
            )
        return [vectors_by_index[index] for index in range(len(texts))]
