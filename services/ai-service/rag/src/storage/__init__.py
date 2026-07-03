"""Provide persistence adapters for every durable RAG subsystem asset.

The storage layer will manage PostgreSQL metadata, pgvector embeddings, BM25
index data, extracted image files, and trace-log persistence. It translates
domain objects into durable representations while keeping storage-specific
details out of core orchestration.

This package does not decide retrieval ranking or ingestion order. Connection
creation and schema initialization must occur through explicit APIs rather
than package-import side effects.
"""
