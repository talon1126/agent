"""Group observability, evaluation, and local management-platform features.

This package will turn ingestion and query traces into structured JSON Lines
logs, quality metrics, and the six-page Streamlit dashboard. Observability
components consume trace and repository contracts without owning ingestion or
retrieval decisions, keeping instrumentation separate from business logic.

Importing the package does not start Streamlit, read log files, or connect to
PostgreSQL; those operations belong to explicit services and scripts.
"""
