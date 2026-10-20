-- Corpus empresarial estructurado para LIVI IA.
-- No duplica la memoria privada de los chats.
CREATE TABLE IF NOT EXISTS livi_knowledge_chunks (
    id BIGSERIAL PRIMARY KEY,
    source_table TEXT NOT NULL,
    chunk_index INTEGER NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    embedding vector(1536),
    row_count INTEGER NOT NULL DEFAULT 0,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_table, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_livi_knowledge_embedding
    ON livi_knowledge_chunks
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 50);

CREATE INDEX IF NOT EXISTS idx_livi_knowledge_source
    ON livi_knowledge_chunks (source_table, chunk_index);
