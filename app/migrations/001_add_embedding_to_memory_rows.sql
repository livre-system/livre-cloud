-- migrations/001_add_embedding_to_memory_rows.sql
-- Livre Cloud — Migración para soporte de embeddings en memory_rows
--
-- Ejecutar UNA SOLA VEZ contra la base de datos de Livre Cloud:
--   psql $DATABASE_URL -f migrations/001_add_embedding_to_memory_rows.sql
--
-- La migración es idempotente: usa IF NOT EXISTS / DO NOTHING.

-- 1. Habilitar la extensión pgvector (si no está habilitada ya).
CREATE EXTENSION IF NOT EXISTS vector;

-- 2. Agregar la columna embedding a memory_rows (nullable: se llena
--    sincrónicamente en uploads y en background para chat_log).
ALTER TABLE memory_rows
    ADD COLUMN IF NOT EXISTS embedding vector(1536);

-- 3. Índice ivfflat para búsqueda ANN eficiente por similitud coseno.
--    Crear DESPUÉS de tener datos suficientes (>= 1000 filas) para mejor
--    calidad de clusters. En desarrollo con pocas filas el índice es
--    opcional pero no hace daño.
--
--    lists=100 es un valor razonable para hasta ~1M vectores.
--    Para colecciones más pequeñas puede reducirse a 10-50.
CREATE INDEX IF NOT EXISTS idx_memory_rows_embedding
    ON memory_rows
    USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);

-- 4. Verificación: mostrar estado de la columna y el índice.
SELECT
    column_name,
    data_type,
    is_nullable
FROM information_schema.columns
WHERE table_name = 'memory_rows'
  AND column_name = 'embedding';

SELECT
    indexname,
    indexdef
FROM pg_indexes
WHERE tablename = 'memory_rows'
  AND indexname = 'idx_memory_rows_embedding';
