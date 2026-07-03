"""Store extracted image bytes and their searchable PostgreSQL index records.

``ImageStorage`` owns the durable boundary for multimodal source assets. Binary
files are written below ``data/images/{collection}/`` (or a configured test
root), while PostgreSQL stores source linkage, dimensions, MIME type, quality
state, hashes, and extensible metadata. Query methods return typed immutable
records so Dashboard and response assembly code do not depend on SQL row order.

The module does not extract images, generate captions, decide quality, or
perform Vision LLM calls. Those responsibilities belong to Loader and
ImageCaptioner stages.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from src.core.errors import DatabaseError, IngestionError
from src.storage.postgres import PostgresPool

_SAFE_PATH_PART = re.compile(r"^\w[\w.-]*$", flags=re.UNICODE)
_SAFE_SUFFIX = re.compile(r"^\.[A-Za-z0-9]+$")


@dataclass(frozen=True, slots=True)
class ImageIndexRecord:
    """Represent one persisted ``image_index`` row.

    Attributes:
        image_id: Stable Python-generated image identifier.
        file_path: Durable local path containing the original image bytes.
        collection_id: Collection that owns the source document.
        document_id: Source document containing the image reference.
        doc_hash: SHA256 digest of the source document.
        page_num: Zero-based or loader-defined source page number when known.
        width: Original or normalized image width in pixels.
        height: Original or normalized image height in pixels.
        mime_type: Image MIME type used by response and Vision adapters.
        image_hash: SHA256 digest of the saved image bytes.
        quality_status: Caption/retrieval quality state constrained by schema.
        metadata: Extensible caption and extraction metadata.
        created_at: Database creation timestamp.
        updated_at: Most recent index update timestamp.
    """

    image_id: str
    file_path: str
    collection_id: str
    document_id: str
    doc_hash: str
    page_num: int | None
    width: int | None
    height: int | None
    mime_type: str | None
    image_hash: str
    quality_status: str
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ImageStorage:
    """Coordinate safe image-file writes with relational image indexing."""

    def __init__(
        self,
        pool: PostgresPool,
        *,
        root_dir: str | Path = Path("data/images"),
    ) -> None:
        """Configure image persistence without creating directories eagerly.

        Args:
            pool: Open PostgreSQL pool used for index writes and reads.
            root_dir: Root directory below which collection folders are created.
                Relative paths are resolved against the current process working
                directory.
        """

        self._pool = pool
        self._root_dir = Path(root_dir).expanduser().resolve()

    def save_image(
        self,
        collection: str,
        image_id: str,
        content: bytes,
        *,
        suffix: str,
    ) -> Path:
        """Write original image bytes below the collection directory.

        Args:
            collection: Stable collection identifier used as one directory name.
            image_id: Stable image identifier used as the filename stem.
            content: Binary image payload extracted by a loader.
            suffix: File extension including the leading dot, for example
                ``.png`` or ``.jpeg``.

        Returns:
            Absolute path of the saved image.

        Raises:
            ValueError: If a path component is blank, contains separators, or
                does not match the restricted portable filename alphabet.
            IngestionError: If directory creation or the atomic file write fails.

        Side Effects:
            Creates ``root_dir/{collection}`` and atomically replaces the target
            file when the same stable image ID is saved again.
        """

        target = self.image_path(collection, image_id, suffix=suffix)
        collection_dir = target.parent
        temporary = target.with_name(f".{target.name}.tmp")
        try:
            collection_dir.mkdir(parents=True, exist_ok=True)
            temporary.write_bytes(content)
            temporary.replace(target)
        except OSError as error:
            temporary.unlink(missing_ok=True)
            raise IngestionError(
                "Unable to persist extracted image",
                context={
                    "operation": "image_save",
                    "collection": collection,
                    "image_id": image_id,
                },
                cause=error,
            ) from error
        return target

    def image_path(
        self,
        collection: str,
        image_id: str,
        *,
        suffix: str,
    ) -> Path:
        """Resolve one managed image path without writing file content.

        Args:
            collection: Stable collection directory name.
            image_id: Stable image filename stem.
            suffix: File extension including the leading dot.

        Returns:
            Absolute path below ``root_dir/{collection}``.

        Raises:
            ValueError: If any path component violates the portable path
                contract.
        """

        self._validate_path_part(collection, field_name="collection")
        self._validate_path_part(image_id, field_name="image_id")
        if not _SAFE_SUFFIX.fullmatch(suffix):
            raise ValueError(
                "suffix must start with '.' and contain only ASCII letters or digits"
            )
        target = (
            self._root_dir / collection / f"{image_id}{suffix.lower()}"
        ).resolve()
        try:
            target.relative_to(self._root_dir)
        except ValueError as error:
            raise ValueError(
                "Managed image path must remain below the configured root directory"
            ) from error
        return target

    def upsert_index(
        self,
        *,
        image_id: str,
        file_path: str | Path,
        collection_id: str,
        document_id: str,
        doc_hash: str,
        image_hash: str,
        page_num: int | None = None,
        width: int | None = None,
        height: int | None = None,
        mime_type: str | None = None,
        quality_status: str = "pending",
        metadata: dict[str, Any] | None = None,
    ) -> ImageIndexRecord:
        """Insert or update one image index row by stable image ID.

        Args:
            image_id: Stable image identifier.
            file_path: Saved local image path.
            collection_id: Collection owning the source document.
            document_id: Existing source document ID.
            doc_hash: SHA256 digest of the source document.
            image_hash: SHA256 digest of the image bytes.
            page_num: Source page number when available.
            width: Image width in pixels.
            height: Image height in pixels.
            mime_type: Image MIME type.
            quality_status: One schema-supported processing quality state.
            metadata: Optional caption, extraction, or loader metadata.

        Returns:
            The complete inserted or updated index record.

        Raises:
            DatabaseError: If document ownership is invalid or PostgreSQL
                rejects the index data.

        Side Effects:
            Inserts or updates one ``image_index`` row.
        """

        with self._pool.transaction() as connection:
            return self.upsert_index_in_transaction(
                connection,
                image_id=image_id,
                file_path=file_path,
                collection_id=collection_id,
                document_id=document_id,
                doc_hash=doc_hash,
                image_hash=image_hash,
                page_num=page_num,
                width=width,
                height=height,
                mime_type=mime_type,
                quality_status=quality_status,
                metadata=metadata,
            )

    def upsert_index_in_transaction(
        self,
        connection: Any,
        *,
        image_id: str,
        file_path: str | Path,
        collection_id: str,
        document_id: str,
        doc_hash: str,
        image_hash: str,
        page_num: int | None = None,
        width: int | None = None,
        height: int | None = None,
        mime_type: str | None = None,
        quality_status: str = "pending",
        metadata: dict[str, Any] | None = None,
    ) -> ImageIndexRecord:
        """Upsert one image index row in a caller-owned transaction.

        Args:
            connection: Active psycopg connection.
            image_id: Stable image identifier.
            file_path: Managed image file path.
            collection_id: Owning collection.
            document_id: Owning source document.
            doc_hash: SHA256 source-document digest.
            image_hash: SHA256 image-byte digest.
            page_num: Source page when available.
            width: Image width when known.
            height: Image height when known.
            mime_type: Image MIME type.
            quality_status: Schema-supported caption quality state.
            metadata: Caption and extraction metadata.

        Returns:
            Complete inserted or updated index record without committing the
            caller transaction.
        """

        row = connection.execute(
                """
                INSERT INTO image_index (
                    image_id,
                    file_path,
                    collection_id,
                    document_id,
                    doc_hash,
                    page_num,
                    width,
                    height,
                    mime_type,
                    image_hash,
                    quality_status,
                    metadata
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (image_id) DO UPDATE SET
                    file_path = EXCLUDED.file_path,
                    collection_id = EXCLUDED.collection_id,
                    document_id = EXCLUDED.document_id,
                    doc_hash = EXCLUDED.doc_hash,
                    page_num = EXCLUDED.page_num,
                    width = EXCLUDED.width,
                    height = EXCLUDED.height,
                    mime_type = EXCLUDED.mime_type,
                    image_hash = EXCLUDED.image_hash,
                    quality_status = EXCLUDED.quality_status,
                    metadata = EXCLUDED.metadata,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING
                    image_id,
                    file_path,
                    collection_id,
                    document_id,
                    doc_hash,
                    page_num,
                    width,
                    height,
                    mime_type,
                    image_hash,
                    quality_status,
                    metadata,
                    created_at,
                    updated_at
                """,
                (
                    image_id,
                    str(Path(file_path).expanduser().resolve()),
                    collection_id,
                    document_id,
                    doc_hash,
                    page_num,
                    width,
                    height,
                    mime_type,
                    image_hash,
                    quality_status,
                    Jsonb(metadata or {}),
                ),
            ).fetchone()

        if row is None:
            raise RuntimeError("image_index upsert returned no row")
        return self._to_record(row)

    def find_by_collection(self, collection_id: str) -> list[ImageIndexRecord]:
        """List image index records for one collection in stable ID order.

        Args:
            collection_id: Collection identifier used by Dashboard filtering.

        Returns:
            Typed records ordered by ``image_id``.

        Raises:
            DatabaseError: If PostgreSQL query execution fails.
        """

        return self._find("collection_id", collection_id)

    def find_by_doc_hash(self, doc_hash: str) -> list[ImageIndexRecord]:
        """List image index records associated with one source document hash.

        Args:
            doc_hash: SHA256 source-document digest.

        Returns:
            Typed records ordered by ``image_id``.

        Raises:
            DatabaseError: If PostgreSQL query execution fails.
        """

        return self._find("doc_hash", doc_hash)

    def find_by_ids(self, image_ids: list[str]) -> list[ImageIndexRecord]:
        """Load image records for response assembly in one database query.

        Args:
            image_ids: Stable image IDs collected from ranked chunk
                ``metadata.image_refs``. Duplicate IDs are accepted because
                response assembly may combine multiple chunks.

        Returns:
            Matching records ordered by ``image_id``. Callers that need
            retrieval-reference order must restore it from their input IDs.
            An empty input returns immediately without opening a connection.

        Raises:
            ValueError: If any image ID is not a non-blank string.
            DatabaseError: If PostgreSQL query execution fails.
        """

        if any(
            not isinstance(image_id, str) or not image_id.strip()
            for image_id in image_ids
        ):
            raise ValueError("image_ids must contain only non-blank strings")
        unique_ids = list(dict.fromkeys(image_ids))
        if not unique_ids:
            return []

        try:
            with self._pool.connection() as connection:
                rows = connection.execute(
                    """
                    SELECT
                        image_id,
                        file_path,
                        collection_id,
                        document_id,
                        doc_hash,
                        page_num,
                        width,
                        height,
                        mime_type,
                        image_hash,
                        quality_status,
                        metadata,
                        created_at,
                        updated_at
                    FROM image_index
                    WHERE image_id = ANY(%s)
                    ORDER BY image_id ASC
                    """,
                    (unique_ids,),
                ).fetchall()
        except DatabaseError:
            raise
        except psycopg.Error as error:
            raise DatabaseError(
                "PostgreSQL image-index batch read failed",
                context={"operation": "image_index_find_by_ids"},
                cause=error,
            ) from error
        return [self._to_record(row) for row in rows]

    def _find(self, column: str, value: str) -> list[ImageIndexRecord]:
        """Execute one allowlisted image-index lookup.

        Args:
            column: Internal allowlisted column selected by a public method.
            value: Bound query value.

        Returns:
            Typed records ordered by stable image ID.
        """

        if column not in {"collection_id", "doc_hash"}:
            raise ValueError(f"Unsupported image-index query column: {column}")
        try:
            with self._pool.connection() as connection:
                rows = connection.execute(
                    f"""
                    SELECT
                        image_id,
                        file_path,
                        collection_id,
                        document_id,
                        doc_hash,
                        page_num,
                        width,
                        height,
                        mime_type,
                        image_hash,
                        quality_status,
                        metadata,
                        created_at,
                        updated_at
                    FROM image_index
                    WHERE {column} = %s
                    ORDER BY image_id ASC
                    """,
                    (value,),
                ).fetchall()
        except DatabaseError:
            raise
        except psycopg.Error as error:
            raise DatabaseError(
                "PostgreSQL image-index read failed",
                context={"operation": f"image_index_find_by_{column}"},
                cause=error,
            ) from error
        return [self._to_record(row) for row in rows]

    @staticmethod
    def _validate_path_part(value: str, *, field_name: str) -> None:
        """Reject untrusted values that could alter filesystem path structure.

        Args:
            value: Candidate collection directory or image filename stem.
            field_name: Public argument name included in validation errors.

        Raises:
            ValueError: If the value is not one safe portable path component.
        """

        if not _SAFE_PATH_PART.fullmatch(value):
            raise ValueError(
                f"{field_name} must be one portable path component containing "
                "only Unicode letters, digits, '.', '_' or '-'"
            )

    @staticmethod
    def _to_record(row: tuple[Any, ...]) -> ImageIndexRecord:
        """Convert one positional PostgreSQL row into a typed immutable record.

        Args:
            row: Complete ``image_index`` projection in dataclass field order.

        Returns:
            ``ImageIndexRecord`` suitable for Dashboard and response assembly.
        """

        return ImageIndexRecord(*row)
