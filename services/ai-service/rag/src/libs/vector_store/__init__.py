"""Define the vector-store component namespace for retrieval storage adapters.

Vector-store implementations will persist and search dense vectors plus related
metadata while keeping query orchestration independent from PostgreSQL/pgvector
details. B7 creates the package boundary; B11 adds the interface, factory, and
pgvector implementation.
"""

__all__: tuple[str, ...] = ()
