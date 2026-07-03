"""Expose application-layer contracts and orchestration for the RAG service.

The core layer owns provider-independent business types, configuration models,
query coordination, response construction, and trace control. It may depend on
the abstract interfaces in ``src.libs`` but must not depend directly on a
specific LLM, embedding provider, vector store, or dashboard implementation.

This package initializer intentionally performs no eager imports. Keeping the
core package side-effect free prevents circular dependencies while later tasks
add configuration and domain types.
"""
