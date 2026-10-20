"""Ingesta del modelo empresarial propio para LIVI IA.

La fuente de verdad sigue siendo PostgreSQL. Este módulo crea una copia
semántica, estructurada y acotada para que LIVI pueda recuperar contexto sin
meter tablas completas en cada prompt.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from psycopg2 import sql

from app.core.database import get_cursor
from app.memory.embeddings import get_embedding, get_embeddings, get_query_embedding
from app.core.encryption import encrypt, decrypt

log = logging.getLogger(__name__)

MAX_CHARS = 6000
EMBEDDING_BATCH_SIZE = 64
# Tablas que contienen secretos, índices internos o conversaciones privadas.
EXCLUDED_TABLES = {
    "credentials",
    "memory_users",
    "memory_documents",
    "memory_rows",
    "livi_knowledge_chunks",
    "schema_migrations",
    "chat_messages",
    "chats",
    "payment_pending",
}
SENSITIVE_KEY = re.compile(
    r"(password|passwd|secret|token|refresh|access[_-]?token|api[_-]?key|private[_-]?key|credential)",
    re.IGNORECASE,
)


def _json_default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return str(value)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if SENSITIVE_KEY.search(str(key)) else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _table_name(schema: str, table: str) -> str:
    return f"{schema}.{table}"


def list_business_tables() -> list[tuple[str, str]]:
    """Lista tablas propias sin incluir secretos, memoria ni conversaciones."""
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT table_schema, table_name
            FROM information_schema.tables
            WHERE table_type = 'BASE TABLE'
              AND table_schema NOT IN ('pg_catalog', 'information_schema')
            ORDER BY table_schema, table_name
            """
        )
        return [
            (row["table_schema"], row["table_name"])
            for row in cur.fetchall()
            if row["table_name"] not in EXCLUDED_TABLES
        ]


def _read_table_rows(schema: str, table: str) -> list[dict[str, Any]]:
    query = sql.SQL("SELECT row_to_json(t)::text AS row_json FROM {}.{} t").format(
        sql.Identifier(schema), sql.Identifier(table)
    )
    with get_cursor() as cur:
        cur.execute(query)
        rows = []
        for row in cur.fetchall():
            raw = json.loads(row["row_json"])
            rows.append(_redact(raw))
        return rows


def _build_chunks(source_table: str, rows: list[dict[str, Any]]) -> list[tuple[int, str, int]]:
    """Agrupa registros completos sin partir JSON en medio."""
    chunks: list[tuple[int, str, int]] = []
    current: list[str] = []
    current_chars = 0
    chunk_index = 0
    for row in rows:
        line = json.dumps(row, ensure_ascii=False, sort_keys=True, default=_json_default)
        rendered = f"TABLA: {source_table}\nREGISTRO: {line}"
        if current and current_chars + len(rendered) + 2 > MAX_CHARS:
            chunks.append((chunk_index, "\n\n".join(current), len(current)))
            chunk_index += 1
            current = []
            current_chars = 0
        current.append(rendered)
        current_chars += len(rendered) + 2
    if current:
        chunks.append((chunk_index, "\n\n".join(current), len(current)))
    return chunks


def _needs_embedding(source_table: str, chunk_index: int, content: str) -> bool:
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    with get_cursor() as cur:
        cur.execute(
            "SELECT content_hash FROM livi_knowledge_chunks WHERE source_table = %s AND chunk_index = %s",
            [source_table, chunk_index],
        )
        row = cur.fetchone()
    return not row or row["content_hash"] != content_hash


def _upsert_chunk(
    source_table: str,
    chunk_index: int,
    content: str,
    row_count: int,
    embedding: list[float] | None = None,
) -> str:
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT content_hash FROM livi_knowledge_chunks
            WHERE source_table = %s AND chunk_index = %s
            """,
            [source_table, chunk_index],
        )
        existing = cur.fetchone()
        if existing and existing["content_hash"] == content_hash:
            cur.execute(
                """UPDATE livi_knowledge_chunks SET synced_at = now(), row_count = %s
                   WHERE source_table = %s AND chunk_index = %s""",
                [row_count, source_table, chunk_index],
            )
            return "unchanged"

        if embedding is None:
            embedding = get_embedding(content)
        if not embedding:
            raise RuntimeError("No se pudo generar embedding para el corpus estructurado")
        encrypted_content = encrypt(content)
        cur.execute(
            """
            INSERT INTO livi_knowledge_chunks
                (source_table, chunk_index, content, content_hash, embedding, row_count, synced_at)
            VALUES (%s, %s, %s, %s, %s::vector, %s, now())
            ON CONFLICT (source_table, chunk_index) DO UPDATE SET
                content = EXCLUDED.content,
                content_hash = EXCLUDED.content_hash,
                embedding = EXCLUDED.embedding,
                row_count = EXCLUDED.row_count,
                synced_at = now()
            """,
            [source_table, chunk_index, encrypted_content, content_hash, str(embedding), row_count],
        )
    return "updated" if existing else "created"


def sync_structured_knowledge() -> dict[str, Any]:
    """Reindexa todas las tablas de negocio, registro por registro y chunk por chunk."""
    totals = {"tables": 0, "rows": 0, "chunks": 0, "created": 0, "updated": 0, "unchanged": 0}
    table_results = []
    for schema, table in list_business_tables():
        source = _table_name(schema, table)
        rows = _read_table_rows(schema, table)
        chunks = _build_chunks(source, rows)
        for start in range(0, len(chunks), EMBEDDING_BATCH_SIZE):
            batch = chunks[start : start + EMBEDDING_BATCH_SIZE]
            pending = [item for item in batch if _needs_embedding(source, item[0], item[1])]
            embeddings = get_embeddings([item[1] for item in pending]) if pending else []
            if pending and embeddings is None:
                raise RuntimeError(f"No se pudo generar el lote de embeddings de {source}")
            embedding_by_index = {
                item[0]: vector for item, vector in zip(pending, embeddings or [])
            }
            for index, content, row_count in batch:
                result = _upsert_chunk(
                    source,
                    index,
                    content,
                    row_count,
                    embedding=embedding_by_index.get(index),
                )
                totals[result] += 1
        with get_cursor() as cur:
            cur.execute(
                "DELETE FROM livi_knowledge_chunks WHERE source_table = %s AND chunk_index >= %s",
                [source, len(chunks)],
            )
        totals["tables"] += 1
        totals["rows"] += len(rows)
        totals["chunks"] += len(chunks)
        table_results.append({"table": source, "rows": len(rows), "chunks": len(chunks)})
    return {**totals, "tables_detail": table_results}


def search_structured_knowledge(query: str, limit: int = 12, min_similarity: float = 0.45) -> list[dict[str, Any]]:
    embedding = get_query_embedding(query)
    if not embedding:
        return []
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT source_table, chunk_index, content, row_count,
                   1 - (embedding <=> %s::vector) AS similarity
            FROM livi_knowledge_chunks
            WHERE embedding IS NOT NULL
              AND 1 - (embedding <=> %s::vector) >= %s
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            [str(embedding), str(embedding), min_similarity, str(embedding), limit],
        )
        results = []
        for row in cur.fetchall():
            try:
                content = decrypt(row["content"])
            except Exception:
                content = row["content"]
            results.append({**row, "content": content})
        return results


def structured_knowledge_status() -> dict[str, Any]:
    with get_cursor() as cur:
        cur.execute(
            """SELECT COUNT(*) AS chunks, COALESCE(SUM(row_count), 0) AS rows,
                      MAX(synced_at) AS last_sync
               FROM livi_knowledge_chunks"""
        )
        row = cur.fetchone() or {"chunks": 0, "rows": 0, "last_sync": None}
    return {
        "chunks": row["chunks"],
        "rows": row["rows"],
        "last_sync": row["last_sync"].isoformat() if row["last_sync"] else None,
    }
