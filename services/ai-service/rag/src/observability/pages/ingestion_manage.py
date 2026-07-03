"""Render the Ingestion Management page for the local Streamlit Dashboard.

This page gathers operator intent for offline ingestion and document lifecycle
management. When an ingestion operation service is supplied by the Dashboard
application, the page submits the request through that service so the operator
sees a real ingestion result instead of a placeholder status.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.observability.services import (
    ConfigOverview,
    ConfigReaderService,
    DataBrowserService,
    DocumentBrowserRow,
    IngestionOperationRequest,
    IngestionOperationResult,
    IngestionOperationService,
    UploadedIngestionFile,
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


@dataclass(frozen=True, slots=True)
class _UploadCandidate:
    """Represent one uploaded file displayed for operator confirmation."""

    filename: str
    content: bytes


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
    ingestion_service: IngestionOperationService | None = None,
) -> IngestionManageSelection:
    """Render ingestion controls and optionally run a submitted ingestion.

    Args:
        model: Render-ready management data.
        ui: Optional Streamlit-like module. ``None`` imports ``streamlit`` at
            call time for real Dashboard usage.
        ingestion_service: Optional service that executes real ingestion when
            the operator clicks ``Run ingestion``. Tests may pass a fake service
            to verify submission without model or database side effects.

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
    uploaded_files = _read_uploaded_files(
        streamlit.file_uploader(
            "Choose files",
            type=("md", "markdown", "pdf"),
            accept_multiple_files=True,
        )
    )
    uploaded_folder_files = _read_uploaded_files(
        streamlit.file_uploader(
            "Choose folder",
            type=("md", "markdown", "pdf"),
            accept_multiple_files="directory",
        )
    )
    path_candidates = _discover_path_candidates(
        ingestion_service,
        source_path,
    )
    selected_source_paths, selected_uploads = _render_candidate_selection(
        streamlit,
        path_candidates=path_candidates,
        uploaded_files=(*uploaded_files, *uploaded_folder_files),
    )
    submit_ingest = bool(streamlit.button("Run ingestion"))
    if submit_ingest:
        if ingestion_service is None:
            streamlit.warning(
                {
                    "collection": model.collection_id,
                    "source_path": source_path,
                    "force": force,
                    "status": "service_unavailable",
                    "error": "Ingestion operation service is not configured.",
                }
            )
        else:
            _render_ingestion_result(
                streamlit,
                ingestion_service.run_ingestion(
                    IngestionOperationRequest(
                        collection=model.collection_id,
                        source_path=source_path,
                        force=force,
                        source_paths=selected_source_paths,
                        uploaded_files=selected_uploads,
                    )
                ),
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


def _read_uploaded_files(uploaded: Any) -> tuple[_UploadCandidate, ...]:
    """Convert Streamlit uploaded files into page-local upload candidates."""

    if uploaded is None:
        return ()
    uploaded_items = uploaded if isinstance(uploaded, list | tuple) else (uploaded,)
    candidates: list[_UploadCandidate] = []
    for item in uploaded_items:
        name = str(getattr(item, "name", "")).strip()
        if not name:
            continue
        content = item.getvalue() if hasattr(item, "getvalue") else b""
        candidates.append(_UploadCandidate(filename=name, content=bytes(content)))
    return tuple(candidates)


def _discover_path_candidates(
    ingestion_service: IngestionOperationService | None,
    source_path: str,
) -> tuple[str, ...]:
    """Discover local file candidates through the ingestion operation service."""

    if (
        ingestion_service is None
        or not hasattr(ingestion_service, "discover_source_candidates")
        or not source_path.strip()
    ):
        return (source_path,) if source_path.strip() else ()
    return tuple(ingestion_service.discover_source_candidates(source_path))


def _render_candidate_selection(
    streamlit: Any,
    *,
    path_candidates: tuple[str, ...],
    uploaded_files: tuple[_UploadCandidate, ...],
) -> tuple[tuple[str, ...], tuple[UploadedIngestionFile, ...]]:
    """Render selectable ingestion candidates and return only checked items."""

    if not path_candidates and not uploaded_files:
        streamlit.info("No ingestion candidates selected.")
        return (), ()

    streamlit.subheader("Ingestion Candidates")
    selected_paths = tuple(
        candidate
        for candidate in path_candidates
        if streamlit.checkbox(f"Ingest path: {candidate}", value=True)
    )
    selected_uploads = tuple(
        UploadedIngestionFile(filename=candidate.filename, content=candidate.content)
        for candidate in uploaded_files
        if streamlit.checkbox(f"Ingest upload: {candidate.filename}", value=True)
    )
    streamlit.dataframe(
        [
            {"type": "path", "source": candidate, "selected": candidate in selected_paths}
            for candidate in path_candidates
        ]
        + [
            {
                "type": "upload",
                "source": candidate.filename,
                "selected": any(
                    upload.filename == candidate.filename for upload in selected_uploads
                ),
            }
            for candidate in uploaded_files
        ]
    )
    return selected_paths, selected_uploads


def _render_ingestion_result(
    streamlit: Any,
    result: IngestionOperationResult,
) -> None:
    """Render a real Dashboard ingestion operation result."""

    payload = {
        "collection": result.collection,
        "source_path": result.source_path,
        "force": result.force,
        "status": result.status,
        "processed": result.processed,
        "trace_ids": list(result.trace_ids),
        "sources": list(result.source_paths),
        "summary": dict(result.summary),
    }
    if result.error:
        payload["error"] = result.error

    if result.status == "success":
        streamlit.success(payload)
    elif result.status == "skipped":
        streamlit.info(payload)
    else:
        streamlit.warning(payload)


def _document_table_row(document: DocumentBrowserRow) -> dict[str, object]:
    """Convert one document DTO into a compact Dashboard table row."""

    return {
        "document_id": document.document_id,
        "title": document.title,
        "source_path": document.source_path,
        "lifecycle_status": document.lifecycle_status,
        "created_at": document.created_at,
        "updated_at": document.updated_at,
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
