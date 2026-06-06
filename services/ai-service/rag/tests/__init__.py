"""Mark the automated verification suite for the modular RAG subsystem.

Tests are organized by behavioral scope: fast unit tests protect isolated
contracts, integration tests validate component collaboration, and end-to-end
tests protect complete ingestion and query flows. External databases and model
providers must be replaced with fakes or isolated behind explicit markers
unless a test is intentionally exercising those integrations.
"""
