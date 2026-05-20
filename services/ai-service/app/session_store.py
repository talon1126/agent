import os
import threading
from functools import lru_cache
from typing import Protocol


POSTGRES_SCHEMA_SQL = [
    """
    CREATE TABLE IF NOT EXISTS session_state (
        session_id TEXT PRIMARY KEY,
        source TEXT,
        chat_id TEXT,
        sender_id TEXT,
        state JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_session_state_updated_at
    ON session_state(updated_at)
    """,
    """
    CREATE TABLE IF NOT EXISTS user_profile (
        user_id TEXT PRIMARY KEY,
        source TEXT,
        profile JSONB NOT NULL DEFAULT '{}'::jsonb,
        preferences JSONB NOT NULL DEFAULT '{}'::jsonb,
        summary TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_user_profile_updated_at
    ON user_profile(updated_at)
    """,
]


class SessionStateStore(Protocol):
    def initialize(self) -> None: ...

    def get_last_order_id(self, session_id: str) -> str | None: ...

    def remember_order(
        self,
        session_id: str,
        order_id: str,
        *,
        source: str | None = None,
        chat_id: str | None = None,
        sender_id: str | None = None,
    ) -> None: ...


class InMemorySessionStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, dict[str, str]] = {}
        self._profiles: dict[str, dict[str, str]] = {}

    def initialize(self) -> None:
        return None

    def get_last_order_id(self, session_id: str) -> str | None:
        with self._lock:
            return self._state.get(session_id, {}).get("last_order_id")

    def remember_order(
        self,
        session_id: str,
        order_id: str,
        *,
        source: str | None = None,
        chat_id: str | None = None,
        sender_id: str | None = None,
    ) -> None:
        with self._lock:
            self._state.setdefault(session_id, {})["last_order_id"] = order_id
            if sender_id:
                self._profiles.setdefault(sender_id, {})["last_order_id"] = order_id


class PostgresSessionStore:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url
        self._init_lock = threading.Lock()
        self._initialized = False

    def initialize(self) -> None:
        with self._init_lock:
            if self._initialized:
                return
            with self._connect() as connection:
                with connection.cursor() as cursor:
                    for statement in POSTGRES_SCHEMA_SQL:
                        cursor.execute(statement)
                connection.commit()
            self._initialized = True

    def get_last_order_id(self, session_id: str) -> str | None:
        self.initialize()
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT state->>'last_order_id' FROM session_state WHERE session_id = %s",
                    (session_id,),
                )
                row = cursor.fetchone()
        return row[0] if row else None

    def remember_order(
        self,
        session_id: str,
        order_id: str,
        *,
        source: str | None = None,
        chat_id: str | None = None,
        sender_id: str | None = None,
    ) -> None:
        self.initialize()
        from psycopg.types.json import Jsonb

        state = Jsonb({"last_order_id": order_id})
        with self._connect() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO session_state (session_id, source, chat_id, sender_id, state)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (session_id) DO UPDATE SET
                        source = COALESCE(EXCLUDED.source, session_state.source),
                        chat_id = COALESCE(EXCLUDED.chat_id, session_state.chat_id),
                        sender_id = COALESCE(EXCLUDED.sender_id, session_state.sender_id),
                        state = session_state.state || EXCLUDED.state,
                        updated_at = now()
                    """,
                    (session_id, source, chat_id, sender_id, state),
                )
                if sender_id:
                    cursor.execute(
                        """
                        INSERT INTO user_profile (user_id, source, profile)
                        VALUES (%s, %s, %s)
                        ON CONFLICT (user_id) DO UPDATE SET
                            source = COALESCE(EXCLUDED.source, user_profile.source),
                            profile = user_profile.profile || EXCLUDED.profile,
                            updated_at = now()
                        """,
                        (sender_id, source, state),
                    )
            connection.commit()

    def _connect(self):
        import psycopg

        return psycopg.connect(self.database_url)


@lru_cache(maxsize=1)
def get_session_store() -> SessionStateStore:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if database_url:
        return PostgresSessionStore(database_url)
    return InMemorySessionStore()
