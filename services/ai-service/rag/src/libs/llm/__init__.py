"""Define the LLM component namespace for chat-model clients.

LLM implementations will expose a consistent chat contract for OpenAI, Azure
OpenAI, Ollama, DeepSeek, and test doubles while hiding provider-specific SDK
details from pipeline code. This package only establishes the B7 directory
boundary; B9 adds interfaces, factories, and implementations.
"""

__all__: tuple[str, ...] = ()
