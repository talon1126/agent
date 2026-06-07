-- Define the durable PostgreSQL foundation for RAG collections, documents,
-- and chunks. Python domain objects generate stable string identifiers before
-- persistence, so these tables store those identifiers directly as TEXT
-- primary keys instead of introducing database-generated surrogate IDs.
--
-- This file is intentionally idempotent. Deployment, local development, and
-- the future init_schema() entry point may execute it repeatedly without
-- recreating existing objects or discarding indexed data.

CREATE EXTENSION IF NOT EXISTS vector;

-- Group documents into independently searchable knowledge collections.
-- The collection ID is suitable for configuration, metadata filters, and API
-- parameters; `name` remains separately unique to support future display-name
-- changes without changing the stable identity.
CREATE TABLE IF NOT EXISTS rag_collections (
    id TEXT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_rag_collections_name UNIQUE (name),
    CONSTRAINT chk_rag_collections_id_not_blank CHECK (length(btrim(id)) > 0),
    CONSTRAINT chk_rag_collections_name_not_blank CHECK (length(btrim(name)) > 0),
    CONSTRAINT chk_rag_collections_metadata_object
        CHECK (jsonb_typeof(metadata) = 'object')
);

-- Persist the canonical Document produced by a loader. The stable `id`
-- directly stores core.types.Document.id. `source_hash` supports ingestion
-- deduplication, while the collection/source uniqueness rule identifies the
-- current logical source within one collection.
CREATE TABLE IF NOT EXISTS rag_documents (
    id TEXT PRIMARY KEY,
    collection_id TEXT NOT NULL,
    source_path TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    title TEXT,
    content TEXT NOT NULL,
    lifecycle_status TEXT NOT NULL DEFAULT 'pending',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_rag_documents_collection
        FOREIGN KEY (collection_id)
        REFERENCES rag_collections(id)
        ON DELETE CASCADE,
    CONSTRAINT uq_rag_documents_collection_source
        UNIQUE (collection_id, source_path),
    -- This composite candidate key lets chunks prove that their denormalized
    -- collection ID belongs to the referenced document.
    CONSTRAINT uq_rag_documents_id_collection
        UNIQUE (id, collection_id),
    CONSTRAINT chk_rag_documents_id_not_blank CHECK (length(btrim(id)) > 0),
    CONSTRAINT chk_rag_documents_source_path_not_blank
        CHECK (length(btrim(source_path)) > 0),
    CONSTRAINT chk_rag_documents_source_hash_sha256
        CHECK (source_hash ~ '^[0-9a-fA-F]{64}$'),
    CONSTRAINT chk_rag_documents_content_not_blank
        CHECK (length(btrim(content)) > 0),
    CONSTRAINT chk_rag_documents_lifecycle_status
        CHECK (
            lifecycle_status IN (
                'pending',
                'processing',
                'success',
                'failed',
                'deleted'
            )
        ),
    CONSTRAINT chk_rag_documents_metadata_object
        CHECK (jsonb_typeof(metadata) = 'object')
);

-- Upgrade existing local development databases created before B6. PostgreSQL
-- supports IF NOT EXISTS for columns and indexes, so this remains safe when
-- init_schema() is executed repeatedly.
ALTER TABLE rag_documents
    ADD COLUMN IF NOT EXISTS lifecycle_status TEXT NOT NULL DEFAULT 'pending';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'chk_rag_documents_lifecycle_status'
          AND conrelid = 'rag_documents'::regclass
    ) THEN
        ALTER TABLE rag_documents
            ADD CONSTRAINT chk_rag_documents_lifecycle_status
            CHECK (
                lifecycle_status IN (
                    'pending',
                    'processing',
                    'success',
                    'failed',
                    'deleted'
                )
            );
    END IF;
END $$;

-- Persist retrievable Chunk objects and their dense vectors. Source offsets
-- use the same start-inclusive/end-exclusive contract as core.types.Chunk.
-- The HNSW index uses cosine distance, matching the configured vector store.
CREATE TABLE IF NOT EXISTS rag_chunks (
    id TEXT PRIMARY KEY,
    collection_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    source_ref JSONB,
    heading_path JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    embedding vector(1536),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_rag_chunks_document_collection
        FOREIGN KEY (document_id, collection_id)
        REFERENCES rag_documents(id, collection_id)
        ON DELETE CASCADE,
    CONSTRAINT uq_rag_chunks_document_index
        UNIQUE (document_id, chunk_index),
    CONSTRAINT chk_rag_chunks_id_not_blank CHECK (length(btrim(id)) > 0),
    CONSTRAINT chk_rag_chunks_index CHECK (chunk_index >= 0),
    CONSTRAINT chk_rag_chunks_content_not_blank CHECK (length(btrim(content)) > 0),
    CONSTRAINT chk_rag_chunks_content_hash_sha256
        CHECK (content_hash ~ '^[0-9a-fA-F]{64}$'),
    CONSTRAINT chk_rag_chunks_offsets
        CHECK (start_offset >= 0 AND end_offset > start_offset),
    CONSTRAINT chk_rag_chunks_source_ref_object
        CHECK (source_ref IS NULL OR jsonb_typeof(source_ref) = 'object'),
    CONSTRAINT chk_rag_chunks_heading_path_array
        CHECK (jsonb_typeof(heading_path) = 'array'),
    CONSTRAINT chk_rag_chunks_metadata_object
        CHECK (jsonb_typeof(metadata) = 'object')
);

-- Accelerate collection browsing, document-level deduplication, stable chunk
-- ordering, and chunk-level differential embedding checks.
CREATE INDEX IF NOT EXISTS idx_rag_documents_collection_id
    ON rag_documents (collection_id);

CREATE INDEX IF NOT EXISTS idx_rag_documents_source_hash
    ON rag_documents (source_hash);

CREATE INDEX IF NOT EXISTS idx_rag_documents_lifecycle_status
    ON rag_documents (collection_id, lifecycle_status);

CREATE INDEX IF NOT EXISTS idx_rag_chunks_document_id
    ON rag_chunks (document_id, chunk_index);

CREATE INDEX IF NOT EXISTS idx_rag_chunks_content_hash
    ON rag_chunks (content_hash);

CREATE INDEX IF NOT EXISTS idx_rag_chunks_metadata
    ON rag_chunks USING GIN (metadata);

-- pgvector permits NULL embeddings while ingestion is processing a chunk.
-- PostgreSQL excludes NULL values from this index until DenseEncoder completes.
CREATE INDEX IF NOT EXISTS idx_rag_chunks_embedding
    ON rag_chunks
    USING hnsw (embedding vector_cosine_ops);

-- Persist the sparse statistics required to calculate BM25 scores without
-- re-tokenizing chunk text during query execution. One row represents one
-- term/chunk posting. Document-level replacement deletes stale postings
-- through the chunk foreign key before the new complete snapshot is inserted.
CREATE TABLE IF NOT EXISTS rag_bm25_terms (
    collection_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    chunk_id TEXT NOT NULL,
    term TEXT NOT NULL,
    term_frequency INTEGER NOT NULL,
    document_frequency INTEGER NOT NULL,
    document_length INTEGER NOT NULL,
    average_document_length DOUBLE PRECISION NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (chunk_id, term),
    CONSTRAINT fk_rag_bm25_terms_chunk
        FOREIGN KEY (chunk_id)
        REFERENCES rag_chunks(id)
        ON DELETE CASCADE,
    CONSTRAINT fk_rag_bm25_terms_document_collection
        FOREIGN KEY (document_id, collection_id)
        REFERENCES rag_documents(id, collection_id)
        ON DELETE CASCADE,
    CONSTRAINT chk_rag_bm25_terms_term_not_blank
        CHECK (length(btrim(term)) > 0),
    CONSTRAINT chk_rag_bm25_terms_frequency
        CHECK (term_frequency > 0 AND document_frequency > 0),
    CONSTRAINT chk_rag_bm25_terms_document_length
        CHECK (document_length >= 0),
    CONSTRAINT chk_rag_bm25_terms_average_length
        CHECK (average_document_length >= 0)
);

CREATE INDEX IF NOT EXISTS idx_rag_bm25_terms_collection_term
    ON rag_bm25_terms (collection_id, term);
CREATE INDEX IF NOT EXISTS idx_rag_bm25_terms_document
    ON rag_bm25_terms (document_id, chunk_id);

-- Index extracted image files stored under data/images/{collection}/. The
-- relational keys keep image metadata aligned with its source document while
-- physical dimensions and quality state support caption processing, Dashboard
-- inspection, and multimodal response assembly.
CREATE TABLE IF NOT EXISTS image_index (
    image_id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    collection_id TEXT NOT NULL,
    document_id TEXT NOT NULL,
    doc_hash TEXT NOT NULL,
    page_num INTEGER,
    width INTEGER,
    height INTEGER,
    mime_type TEXT,
    image_hash TEXT NOT NULL,
    quality_status TEXT NOT NULL DEFAULT 'pending',
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_image_index_document_collection
        FOREIGN KEY (document_id, collection_id)
        REFERENCES rag_documents(id, collection_id)
        ON DELETE CASCADE,
    CONSTRAINT chk_image_index_id_not_blank
        CHECK (length(btrim(image_id)) > 0),
    CONSTRAINT chk_image_index_file_path_not_blank
        CHECK (length(btrim(file_path)) > 0),
    CONSTRAINT chk_image_index_doc_hash_sha256
        CHECK (doc_hash ~ '^[0-9a-fA-F]{64}$'),
    CONSTRAINT chk_image_index_page_num
        CHECK (page_num IS NULL OR page_num >= 0),
    CONSTRAINT chk_image_index_dimensions
        CHECK (
            (width IS NULL OR width > 0)
            AND (height IS NULL OR height > 0)
        ),
    CONSTRAINT chk_image_index_image_hash_sha256
        CHECK (image_hash ~ '^[0-9a-fA-F]{64}$'),
    CONSTRAINT chk_image_index_quality_status
        CHECK (quality_status IN ('pending', 'ok', 'low_quality', 'skipped', 'failed')),
    CONSTRAINT chk_image_index_metadata_object
        CHECK (jsonb_typeof(metadata) = 'object')
);

-- Preserve the historical index names required by the image-storage contract.
CREATE INDEX IF NOT EXISTS idx_collection ON image_index (collection_id);
CREATE INDEX IF NOT EXISTS idx_doc_hash ON image_index (doc_hash);
CREATE INDEX IF NOT EXISTS idx_image_index_document_id
    ON image_index (document_id);
CREATE INDEX IF NOT EXISTS idx_image_index_image_hash
    ON image_index (image_hash);

-- Store query traces as four independent JSONB sections. Commonly filtered
-- values remain first-class columns so Dashboard list views do not need to
-- scan nested JSON for collection, status, time range, or request source.
CREATE TABLE IF NOT EXISTS rag_query_traces (
    trace_id TEXT PRIMARY KEY,
    collection_id TEXT NOT NULL,
    raw_query TEXT NOT NULL,
    request_source TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'running',
    basic_info JSONB NOT NULL DEFAULT '{}'::jsonb,
    stages JSONB NOT NULL DEFAULT '[]'::jsonb,
    summary_metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    evaluation_metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    error JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_rag_query_traces_collection
        FOREIGN KEY (collection_id)
        REFERENCES rag_collections(id)
        ON DELETE CASCADE,
    CONSTRAINT chk_rag_query_traces_id_not_blank
        CHECK (length(btrim(trace_id)) > 0),
    CONSTRAINT chk_rag_query_traces_query_not_blank
        CHECK (length(btrim(raw_query)) > 0),
    CONSTRAINT chk_rag_query_traces_request_source_not_blank
        CHECK (length(btrim(request_source)) > 0),
    CONSTRAINT chk_rag_query_traces_finished_at
        CHECK (finished_at IS NULL OR finished_at >= started_at),
    CONSTRAINT chk_rag_query_traces_status
        CHECK (status IN ('running', 'success', 'failed')),
    CONSTRAINT chk_rag_query_traces_basic_info_object
        CHECK (jsonb_typeof(basic_info) = 'object'),
    CONSTRAINT chk_rag_query_traces_stages_array
        CHECK (jsonb_typeof(stages) = 'array'),
    CONSTRAINT chk_rag_query_traces_summary_metrics_object
        CHECK (jsonb_typeof(summary_metrics) = 'object'),
    CONSTRAINT chk_rag_query_traces_evaluation_metrics_object
        CHECK (jsonb_typeof(evaluation_metrics) = 'object'),
    CONSTRAINT chk_rag_query_traces_error_object
        CHECK (error IS NULL OR jsonb_typeof(error) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_rag_query_traces_collection_started_at
    ON rag_query_traces (collection_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_rag_query_traces_status_started_at
    ON rag_query_traces (status, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_rag_query_traces_request_source
    ON rag_query_traces (request_source);

-- Store ingestion traces using the same four-section contract. Source columns
-- support document-oriented history views and SHA256-based trace lookup.
CREATE TABLE IF NOT EXISTS rag_ingestion_traces (
    trace_id TEXT PRIMARY KEY,
    collection_id TEXT NOT NULL,
    source_uri TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    status TEXT NOT NULL DEFAULT 'running',
    basic_info JSONB NOT NULL DEFAULT '{}'::jsonb,
    stages JSONB NOT NULL DEFAULT '[]'::jsonb,
    summary_metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    evaluation_metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    error JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_rag_ingestion_traces_collection
        FOREIGN KEY (collection_id)
        REFERENCES rag_collections(id)
        ON DELETE CASCADE,
    CONSTRAINT chk_rag_ingestion_traces_id_not_blank
        CHECK (length(btrim(trace_id)) > 0),
    CONSTRAINT chk_rag_ingestion_traces_source_uri_not_blank
        CHECK (length(btrim(source_uri)) > 0),
    CONSTRAINT chk_rag_ingestion_traces_source_hash_sha256
        CHECK (source_hash ~ '^[0-9a-fA-F]{64}$'),
    CONSTRAINT chk_rag_ingestion_traces_finished_at
        CHECK (finished_at IS NULL OR finished_at >= started_at),
    CONSTRAINT chk_rag_ingestion_traces_status
        CHECK (status IN ('running', 'success', 'skipped', 'failed')),
    CONSTRAINT chk_rag_ingestion_traces_basic_info_object
        CHECK (jsonb_typeof(basic_info) = 'object'),
    CONSTRAINT chk_rag_ingestion_traces_stages_array
        CHECK (jsonb_typeof(stages) = 'array'),
    CONSTRAINT chk_rag_ingestion_traces_summary_metrics_object
        CHECK (jsonb_typeof(summary_metrics) = 'object'),
    CONSTRAINT chk_rag_ingestion_traces_evaluation_metrics_object
        CHECK (jsonb_typeof(evaluation_metrics) = 'object'),
    CONSTRAINT chk_rag_ingestion_traces_error_object
        CHECK (error IS NULL OR jsonb_typeof(error) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_rag_ingestion_traces_collection_started_at
    ON rag_ingestion_traces (collection_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_rag_ingestion_traces_status_started_at
    ON rag_ingestion_traces (status, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_rag_ingestion_traces_source_hash
    ON rag_ingestion_traces (source_hash);

-- Represent one execution of an evaluation backend. The settings snapshot
-- records the exact retrieval, rerank, model, and dataset configuration needed
-- to compare historical runs and diagnose quality regressions.
CREATE TABLE IF NOT EXISTS rag_evaluation_runs (
    id TEXT PRIMARY KEY,
    collection_id TEXT NOT NULL,
    evaluator TEXT NOT NULL,
    dataset_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    settings_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
    summary JSONB NOT NULL DEFAULT '{}'::jsonb,
    error JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_rag_evaluation_runs_collection
        FOREIGN KEY (collection_id)
        REFERENCES rag_collections(id)
        ON DELETE CASCADE,
    CONSTRAINT chk_rag_evaluation_runs_id_not_blank
        CHECK (length(btrim(id)) > 0),
    CONSTRAINT chk_rag_evaluation_runs_evaluator_not_blank
        CHECK (length(btrim(evaluator)) > 0),
    CONSTRAINT chk_rag_evaluation_runs_dataset_not_blank
        CHECK (length(btrim(dataset_name)) > 0),
    CONSTRAINT chk_rag_evaluation_runs_status
        CHECK (status IN ('pending', 'running', 'success', 'failed')),
    CONSTRAINT chk_rag_evaluation_runs_times
        CHECK (
            (started_at IS NULL OR started_at >= created_at)
            AND (finished_at IS NULL OR started_at IS NOT NULL)
            AND (finished_at IS NULL OR finished_at >= started_at)
        ),
    CONSTRAINT chk_rag_evaluation_runs_settings_object
        CHECK (jsonb_typeof(settings_snapshot) = 'object'),
    CONSTRAINT chk_rag_evaluation_runs_summary_object
        CHECK (jsonb_typeof(summary) = 'object'),
    CONSTRAINT chk_rag_evaluation_runs_error_object
        CHECK (error IS NULL OR jsonb_typeof(error) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_rag_evaluation_runs_collection_created_at
    ON rag_evaluation_runs (collection_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_rag_evaluation_runs_status_created_at
    ON rag_evaluation_runs (status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_rag_evaluation_runs_evaluator
    ON rag_evaluation_runs (evaluator);

-- Store one metric per row so Dashboard can compare trends without extracting
-- dynamic metric names from JSON. Details retain evaluator-specific evidence,
-- thresholds, sample counts, and per-question observations.
CREATE TABLE IF NOT EXISTS rag_evaluation_results (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    metric_value DOUBLE PRECISION NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_rag_evaluation_results_run
        FOREIGN KEY (run_id)
        REFERENCES rag_evaluation_runs(id)
        ON DELETE CASCADE,
    CONSTRAINT uq_rag_evaluation_results_run_metric
        UNIQUE (run_id, metric_name),
    CONSTRAINT chk_rag_evaluation_results_id_not_blank
        CHECK (length(btrim(id)) > 0),
    CONSTRAINT chk_rag_evaluation_results_metric_not_blank
        CHECK (length(btrim(metric_name)) > 0),
    CONSTRAINT chk_rag_evaluation_results_metric_finite
        CHECK (
            metric_value NOT IN (
                'NaN'::DOUBLE PRECISION,
                'Infinity'::DOUBLE PRECISION,
                '-Infinity'::DOUBLE PRECISION
            )
        ),
    CONSTRAINT chk_rag_evaluation_results_details_object
        CHECK (jsonb_typeof(details) = 'object')
);

CREATE INDEX IF NOT EXISTS idx_rag_evaluation_results_run_id
    ON rag_evaluation_results (run_id);
CREATE INDEX IF NOT EXISTS idx_rag_evaluation_results_metric_created_at
    ON rag_evaluation_results (metric_name, created_at DESC);
