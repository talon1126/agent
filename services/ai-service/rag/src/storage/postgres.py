"""Provide the PostgreSQL connection-pool and schema-initialization boundary.

This module owns database connection lifecycle for repositories, ingestion,
retrieval, evaluation, and observability services. It converts validated
``DatabaseSettings`` plus environment variables into a lazy psycopg connection
pool, exposes explicit connection and transaction contexts, and executes the
canonical idempotent schema in one transaction.

The module does not contain repository queries, choose database credentials, or
load the complete application settings document. Callers must pass validated
database settings and control when the pool opens and closes. Public failures
use ``ConfigurationError`` or ``DatabaseError`` with trace-safe context; DSNs
and credentials are never copied into messages or diagnostic metadata.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
from psycopg import Connection
from psycopg_pool import ConnectionPool

from src.core.config import DatabaseSettings
from src.core.errors import ConfigurationError, DatabaseError

DEFAULT_SCHEMA_PATH = Path(__file__).with_name("schema.sql")
POOL_NAME = "aimodel-rag"


class PostgresPool:
    """Manage one lazy psycopg connection pool for the RAG subsystem.

    Instances are created through ``from_settings()`` so database selection
    remains configuration-driven. Construction performs no network I/O;
    application startup calls ``open()``, and shutdown calls ``close()``.

    Attributes:
        is_open: Whether this wrapper has successfully opened its driver pool.
    """

    def __init__(self, driver_pool: ConnectionPool[Connection[Any]]) -> None:
        """Wrap an already configured psycopg pool.

        Args:
            driver_pool: Lazy driver pool created with ``open=False``.

        Notes:
            The constructor is intentionally small. Production callers should
            use ``from_settings()`` instead of constructing driver pools
            directly, while tests may inject a controlled pool double.
        """

        self._driver_pool = driver_pool
        self._is_open = False

    @classmethod
    def from_settings(
        cls,
        settings: DatabaseSettings,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> PostgresPool:
        """Create a lazy connection pool from validated settings.

        Args:
            settings: Database configuration naming the DSN environment
                variable and maximum connection count.
            environ: Environment mapping used to resolve the DSN. ``None`` uses
                ``os.environ``; tests may pass an isolated mapping.

        Returns:
            A ``PostgresPool`` that has not opened network connections.

        Raises:
            ConfigurationError: If the configured provider is not PostgreSQL or
                the named DSN environment variable is absent or blank.
        """

        if settings.provider.lower() != "postgresql":
            raise ConfigurationError(
                "RAG storage requires the PostgreSQL database provider",
                context={"provider": settings.provider},
            )

        environment = os.environ if environ is None else environ
        connection_info = environment.get(settings.url_env, "").strip()
        if not connection_info:
            raise ConfigurationError(
                f"Missing PostgreSQL connection environment variable: "
                f"{settings.url_env}",
                context={"environment_variable": settings.url_env},
            )

        driver_pool = ConnectionPool(
            connection_info,
            min_size=1,
            max_size=settings.pool_size,
            open=False,
            name=POOL_NAME,
        )
        return cls(driver_pool)

    @property
    def is_open(self) -> bool:
        """Return whether ``open()`` completed without a driver failure."""

        return self._is_open

    def __repr__(self) -> str:
        """Return lifecycle metadata without exposing the driver's DSN."""

        return (
            f"{type(self).__name__}(name={POOL_NAME!r}, "
            f"is_open={self._is_open!r})"
        )

    def open(self) -> None:
        """Open the pool and wait until its minimum connection is available.

        Raises:
            DatabaseError: If psycopg cannot establish the minimum pool
                capacity. The original driver error is retained as ``cause``.

        Side Effects:
            Starts psycopg pool workers and establishes database connections.
        """

        if self._is_open:
            return

        try:
            self._driver_pool.open(wait=True)
        except Exception as error:
            # ``open(wait=True)`` may start workers before minimum-capacity
            # validation fails. Best-effort cleanup prevents partially opened
            # pools from surviving a failed application startup.
            try:
                self._driver_pool.close()
            except Exception:
                pass
            raise DatabaseError(
                "Unable to open PostgreSQL connection pool",
                context={"operation": "pool_open"},
                cause=error,
            ) from error
        self._is_open = True

    def close(self) -> None:
        """Close all pooled connections and stop background workers.

        The method is idempotent so application shutdown may call it after a
        partial startup failure.

        Raises:
            DatabaseError: If the driver fails while closing an open pool.

        Side Effects:
            Releases active PostgreSQL connections owned by this pool.
        """

        if not self._is_open:
            return

        try:
            self._driver_pool.close()
        except Exception as error:
            raise DatabaseError(
                "Unable to close PostgreSQL connection pool",
                context={"operation": "pool_close"},
                cause=error,
            ) from error
        self._is_open = False

    @contextmanager
    def connection(self) -> Iterator[Connection[Any]]:
        """Borrow one connection and return it when the context exits.

        Yields:
            A live psycopg connection managed by the driver pool.

        Raises:
            DatabaseError: If the pool is closed, connection acquisition fails,
                or driver cleanup fails.

        Notes:
            Exceptions raised by caller code inside the context are preserved.
            Database operations that require normalized error handling should
            execute inside ``transaction()`` or wrap psycopg errors at their
            repository boundary.
        """

        if not self._is_open:
            raise DatabaseError(
                "PostgreSQL connection pool is not open",
                context={"operation": "connection_acquire"},
            )

        manager = self._driver_pool.connection()
        try:
            connection = manager.__enter__()
        except Exception as error:
            raise DatabaseError(
                "Unable to acquire PostgreSQL connection",
                context={"operation": "connection_acquire"},
                cause=error,
            ) from error

        try:
            yield connection
        except BaseException:
            manager.__exit__(*sys.exc_info())
            raise
        else:
            try:
                manager.__exit__(None, None, None)
            except Exception as error:
                raise DatabaseError(
                    "Unable to release PostgreSQL connection",
                    context={"operation": "connection_release"},
                    cause=error,
                ) from error

    @contextmanager
    def transaction(self) -> Iterator[Connection[Any]]:
        """Run caller operations in a commit-or-rollback transaction.

        Yields:
            A pooled connection with an active psycopg transaction.

        Raises:
            DatabaseError: If a psycopg operation, commit, or rollback fails.
            Exception: Non-database caller exceptions are re-raised unchanged
                after psycopg rolls back the transaction.

        Side Effects:
            Commits on normal exit and rolls back on exceptional exit.
        """

        try:
            with self.connection() as connection, connection.transaction():
                yield connection
        except DatabaseError:
            raise
        except psycopg.Error as error:
            raise DatabaseError(
                "PostgreSQL transaction failed",
                context={"operation": "transaction"},
                cause=error,
            ) from error

    def health_check(self) -> bool:
        """Verify the pool can execute a minimal PostgreSQL query.

        Returns:
            ``True`` when PostgreSQL returns the expected scalar value.

        Raises:
            DatabaseError: If acquiring a connection or executing ``SELECT 1``
                fails, or PostgreSQL returns an unexpected result.
        """

        try:
            with self.connection() as connection:
                result = connection.execute("SELECT 1").fetchone()
        except DatabaseError:
            raise
        except psycopg.Error as error:
            raise DatabaseError(
                "PostgreSQL health check failed",
                context={"operation": "health_check"},
                cause=error,
            ) from error

        if result != (1,):
            raise DatabaseError(
                "PostgreSQL health check returned an unexpected result",
                context={"operation": "health_check"},
            )
        return True


def init_schema(
    pool: PostgresPool,
    *,
    schema_path: str | Path = DEFAULT_SCHEMA_PATH,
) -> None:
    """Initialize the canonical PostgreSQL schema in one transaction.

    Args:
        pool: Open ``PostgresPool`` used to execute schema DDL.
        schema_path: SQL file to execute. The default is the checked-in
            ``src/storage/schema.sql`` resource.

    Raises:
        DatabaseError: If the SQL file cannot be read or PostgreSQL rejects any
            statement. File errors include only the path; SQL errors retain the
            original psycopg exception without exposing connection credentials.

    Side Effects:
        Creates missing extensions, tables, constraints, and indexes. The
        canonical SQL uses ``IF NOT EXISTS``, so repeated calls are supported.
    """

    resolved_path = Path(schema_path).expanduser().resolve()
    try:
        schema_sql = resolved_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise DatabaseError(
            "Unable to read PostgreSQL schema file",
            context={
                "operation": "schema_read",
                "schema_path": str(resolved_path),
            },
            cause=error,
        ) from error

    if not schema_sql.strip():
        raise DatabaseError(
            "PostgreSQL schema file is empty",
            context={
                "operation": "schema_read",
                "schema_path": str(resolved_path),
            },
        )

    try:
        with pool.transaction() as connection:
            connection.execute(schema_sql)
    except DatabaseError as error:
        if error.context.get("operation") == "transaction":
            raise DatabaseError(
                "Unable to initialize PostgreSQL schema",
                context={
                    "operation": "schema_execute",
                    "schema_path": str(resolved_path),
                },
                cause=error.cause or error,
            ) from error
        raise
