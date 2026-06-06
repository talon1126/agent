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
    CONSTRAINT chk_rag_documents_metadata_object
        CHECK (jsonb_typeof(metadata) = 'object')
);

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
