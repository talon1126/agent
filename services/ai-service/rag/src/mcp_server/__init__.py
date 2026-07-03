"""Expose the Model Context Protocol boundary of the RAG subsystem.

The package will adapt internal retrieval capabilities into stable MCP tools
such as knowledge queries, collection discovery, and document summaries. It is
responsible for MCP schemas and transport-facing error content, not retrieval
ranking, persistence, or answer-generation policy.

Imports remain side-effect free until the explicit MCP server factory or
runtime entry point is called.
"""
