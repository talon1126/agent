"""Expose Streamlit page modules for the local RAG Dashboard.

The pages package contains thin rendering functions only. It does not open
PostgreSQL pools, run ingestion jobs, delete documents, or start Streamlit by
itself. Dashboard composition code wires services into page model builders and
then passes the resulting models to these render functions.
"""

