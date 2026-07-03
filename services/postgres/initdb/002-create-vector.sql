-- Enable the vector data type and index access methods for RAG embeddings.
-- Existing databases may create this extension through the idempotent RAG
-- schema; this initialization script covers newly provisioned volumes.
CREATE EXTENSION IF NOT EXISTS vector;
