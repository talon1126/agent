"""Render the Ingestion Management page for the local Streamlit Dashboard.

This page gathers operator intent for offline ingestion and document lifecycle
management, but it deliberately does not run those side effects inside the
renderer. Later Dashboard composition code can pass the returned selection to a
safe orchestration service that invokes ``ingest.py`` or document deletion
workflows with trace recording.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.observability.services import (
    ConfigOverview,
    ConfigReaderService,
    DataBrowserService,
    DocumentBrowserRow,
)


@dataclass(frozen=True, slots=True)
class IngestionManagePageModel:
    """Collect read-only data needed by the ingestion management page."""

    collection_id: str
    raw_data_dir: str
    documents: tuple[DocumentBrowserRow, ...]


@dataclass(frozen=True, slots=True)
class IngestionManageSelection:
    """Represent operator intent collected from ingestion management controls.

    Page rendering returns this DTO so the Dashboard application can decide
    whether to call a real ingestion or deletion orchestration service. This
    prevents unit and page tests from accidentally mutating PostgreSQL or file
    storage while still verifying that the UI collects the correct fields.
    """

    collection_id: str
    source_path: str
    force: bool
    submit_ingest: bool
    delete_document_id: str | None
    submit_delete: bool


def build_ingestion_manage_page_model(
    *,
    config_reader: ConfigReaderService,
    data_browser: DataBrowserService,
    collection_id: str | None = None,
) -> IngestionManagePageModel:
    """Read services and build a render-ready ingestion management model.

    Args:
        config_reader: Service that projects validated settings and raw data
            paths.
        data_browser: Service that lists currently indexed documents.
        collection_id: Optional collection override. ``None`` uses the default
            collection from settings.

    Returns:
        Immutable page model containing collection identity, source root, and
        current document rows for deletion selection.
    """

    config: ConfigOverview = config_reader.read_overview()
    selected_collection = collection_id or config.default_collection
    documents = tuple(data_browser.list_documents(selected_collection))
    return IngestionManagePageModel(
        collection_id=selected_collection,
        raw_data_dir=config.paths.get("raw_data_dir", ""),
        documents=documents,
    )


def render_ingestion_manage_page(
    model: IngestionManagePageModel,
    *,
    ui: Any | None = None,
) -> IngestionManageSelection:
    """Render ingestion controls and return the collected operator intent.

    Args:
        model: Render-ready management data.
        ui: Optional Streamlit-like module. ``None`` imports ``streamlit`` at
            call time for real Dashboard usage.

    Returns:
        Selection DTO containing the requested source path, force flag,
        ingestion submit state, selected document ID, and delete submit state.

    Side Effects:
        Emits Streamlit calls only. It does not ingest files, delete documents,
        or open database transactions.
    """

    streamlit = ui or _streamlit()
    streamlit.title("Ingestion Management")
    streamlit.caption(f"Collection: {model.collection_id}")

    streamlit.subheader("Document Ingestion")
    source_path = streamlit.text_input(
        "Source path",
        value=model.raw_data_dir,
        help="Markdown or PDF file path, or a directory supported by ingest.py.",
    )
    force = bool(streamlit.checkbox("Force rebuild", value=False))
    submit_ingest = bool(streamlit.button("Run ingestion"))
    if submit_ingest:
        streamlit.info(
            {
                "collection": model.collection_id,
                "source_path": source_path,
                "force": force,
                "status": "pending orchestration",
            }
        )

    streamlit.subheader("Indexed Documents")
    streamlit.dataframe([_document_table_row(document) for document in model.documents])
    selected_document = _selected_document_id(streamlit, model.documents)
    submit_delete = bool(streamlit.button("Delete selected document"))
    if submit_delete and selected_document:
        streamlit.warning(
            {
                "document_id": selected_document,
                "status": "pending deletion orchestration",
            }
        )
    elif submit_delete:
        streamlit.warning("No indexed document is available for deletion.")

    return IngestionManageSelection(
        collection_id=model.collection_id,
        source_path=source_path,
        force=force,
        submit_ingest=submit_ingest,
        delete_document_id=selected_document,
        submit_delete=submit_delete,
    )


def _document_table_row(document: DocumentBrowserRow) -> dict[str, object]:
    """Convert one document DTO into a compact Dashboard table row."""

    return {
        "document_id": document.document_id,
        "title": document.title,
        "source_path": document.source_path,
        "lifecycle_status": document.lifecycle_status,
        "chunks": document.chunk_count,
        "images": document.image_count,
    }


def _selected_document_id(
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


def _streamlit() -> Any:
    """Import Streamlit only when a real render call needs it."""

    import streamlit

    return streamlit
