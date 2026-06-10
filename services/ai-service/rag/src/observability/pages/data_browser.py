"""Render the Data Browser page for indexed RAG assets.

The Data Browser page lets operators inspect what has actually been indexed:
document rows, chunk details, source references, image references, and dense or
BM25 readiness. The module only projects existing Dashboard service DTOs into
Streamlit calls. It never edits documents, rebuilds indexes, or reads storage
tables directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.observability.services import (
    ChunkBrowserRow,
    DataBrowserService,
    DocumentBrowserRow,
    ImageBrowserRow,
)


@dataclass(frozen=True, slots=True)
class DataBrowserPageModel:
    """Collect document, chunk, image, and selected-detail data for rendering."""

    collection_id: str
    documents: tuple[DocumentBrowserRow, ...]
    chunks: tuple[ChunkBrowserRow, ...]
    images: tuple[ImageBrowserRow, ...]
    selected_chunk: ChunkBrowserRow | None


@dataclass(frozen=True, slots=True)
class DataBrowserSelection:
    """Represent the currently selected document and chunk IDs."""

    document_id: str | None
    chunk_id: str | None


def build_data_browser_page_model(
    *,
    data_browser: DataBrowserService,
    collection_id: str,
    document_id: str | None = None,
    chunk_id: str | None = None,
) -> DataBrowserPageModel:
    """Read Dashboard data services and build a render-ready page model.

    Args:
        data_browser: Service that reads documents, chunks, images, and chunk
            details from PostgreSQL projections.
        collection_id: Knowledge collection to inspect.
        document_id: Optional document preselection. ``None`` selects the first
            available document.
        chunk_id: Optional chunk preselection. ``None`` selects the first chunk
            for the selected document.

    Returns:
        Immutable page model containing table rows and selected chunk details.
    """

    documents = tuple(data_browser.list_documents(collection_id))
    selected_document_id = document_id or (documents[0].document_id if documents else None)
    chunks = (
        tuple(data_browser.list_chunks(selected_document_id))
        if selected_document_id is not None
        else ()
    )
    selected_chunk_id = chunk_id or (chunks[0].chunk_id if chunks else None)
    selected_chunk = (
        data_browser.get_chunk_detail(selected_chunk_id)
        if selected_chunk_id is not None
        else None
    )
    images = tuple(data_browser.list_images(collection_id))
    return DataBrowserPageModel(
        collection_id=collection_id,
        documents=documents,
        chunks=chunks,
        images=images,
        selected_chunk=selected_chunk,
    )


def render_data_browser_page(
    model: DataBrowserPageModel,
    *,
    ui: Any | None = None,
) -> DataBrowserSelection:
    """Render document/chunk/image tables and return selected IDs.

    Args:
        model: Render-ready data browser model.
        ui: Optional Streamlit-like module. ``None`` imports ``streamlit`` at
            call time for real Dashboard usage.

    Returns:
        Selection DTO containing the selected document and chunk IDs.

    Side Effects:
        Emits Streamlit calls only. It does not modify document lifecycle,
        chunk rows, image rows, or index visibility.
    """

    streamlit = ui or _streamlit()
    streamlit.title("Data Browser")
    streamlit.caption(f"Collection: {model.collection_id}")

    streamlit.subheader("Documents")
    streamlit.dataframe([_document_row(document) for document in model.documents])
    selected_document_id = _select_document(streamlit, model.documents)

    streamlit.subheader("Chunks")
    streamlit.dataframe([_chunk_row(chunk) for chunk in model.chunks])
    selected_chunk_id = _select_chunk(streamlit, model.chunks)

    streamlit.subheader("Chunk Detail")
    if model.selected_chunk is None:
        streamlit.info("No chunk detail is available.")
    else:
        streamlit.write(
            {
                "chunk_id": model.selected_chunk.chunk_id,
                "text": model.selected_chunk.text,
                "metadata": dict(model.selected_chunk.metadata),
                "source_ref": model.selected_chunk.source_ref,
                "image_refs": model.selected_chunk.image_refs,
            }
        )

    streamlit.subheader("Images")
    streamlit.dataframe([_image_row(image) for image in model.images])
    return DataBrowserSelection(
        document_id=selected_document_id,
        chunk_id=selected_chunk_id,
    )


def _document_row(document: DocumentBrowserRow) -> dict[str, object]:
    """Convert a document DTO into a table row."""

    return {
        "document_id": document.document_id,
        "title": document.title,
        "source_path": document.source_path,
        "status": document.lifecycle_status,
        "created_at": document.created_at,
        "updated_at": document.updated_at,
        "chunks": document.chunk_count,
        "images": document.image_count,
    }


def _chunk_row(chunk: ChunkBrowserRow) -> dict[str, object]:
    """Convert a chunk DTO into a table row for scan-heavy browsing."""

    return {
        "chunk_id": chunk.chunk_id,
        "chunk_index": chunk.chunk_index,
        "preview": chunk.text_preview,
        "dense_indexed": chunk.dense_indexed,
        "bm25_terms": chunk.bm25_term_count,
        "image_refs": ", ".join(chunk.image_refs),
    }


def _image_row(image: ImageBrowserRow) -> dict[str, object]:
    """Convert an image DTO into a table row."""

    return {
        "image_id": image.image_id,
        "document_id": image.document_id,
        "file_path": image.file_path,
        "page_num": image.page_num,
        "size": _image_size(image),
        "quality_status": image.quality_status,
        "created_at": image.created_at,
        "updated_at": image.updated_at,
    }


def _select_document(
    streamlit: Any,
    documents: tuple[DocumentBrowserRow, ...],
) -> str | None:
    """Render document selection and return the selected document ID."""

    options = tuple(document.document_id for document in documents)
    if not options:
        streamlit.info("No indexed documents are available.")
        return None
    selected = streamlit.selectbox("Document", options=options)
    return str(selected) if selected is not None else None


def _select_chunk(streamlit: Any, chunks: tuple[ChunkBrowserRow, ...]) -> str | None:
    """Render chunk selection and return the selected chunk ID."""

    options = tuple(chunk.chunk_id for chunk in chunks)
    if not options:
        streamlit.info("No chunks are available for the selected document.")
        return None
    selected = streamlit.selectbox("Chunk", options=options)
    return str(selected) if selected is not None else None


def _image_size(image: ImageBrowserRow) -> str:
    """Return a compact image dimension label for table display."""

    if image.width is None or image.height is None:
        return "unknown"
    return f"{image.width}x{image.height}"


def _streamlit() -> Any:
    """Import Streamlit only when a real render call needs it."""

    import streamlit

    return streamlit
