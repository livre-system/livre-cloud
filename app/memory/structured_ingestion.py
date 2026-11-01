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
import threading
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
    "livre_trip_messages",
    "livre_handie_sessions",
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


def index_conversation_message(
    message_id: int,
    trip_id: int | None,
    channel: str,
    sender_role: str,
    body: str,
    created_at: Any = None,
) -> None:
    """Indexa texto de conversaciones operativas sin copiar audio/base64."""
    text = (body or "").strip()[:MAX_CHARS]
    if not text:
        return
    source = f"conversation.message:{message_id}"
    content = (
        "FUENTE: conversación operativa de Livre\n"
        f"MENSAJE_ID: {message_id}\n"
        f"VIAJE_ID: {trip_id if trip_id is not None else 'sin_viaje'}\n"
        f"CANAL: {channel}\n"
        f"ROL: {sender_role}\n"
        f"FECHA: {created_at.isoformat() if hasattr(created_at, 'isoformat') else created_at or 'sin_fecha'}\n"
        f"TEXTO: {text}"
    )
    try:
        _upsert_chunk(source, 0, content, 1)
        log.info("Conversación indexada para LIVI IA: message_id=%s", message_id)
    except Exception:
        log.exception("No se pudo indexar conversación message_id=%s", message_id)


def schedule_conversation_message(
    message_id: int,
    trip_id: int | None,
    channel: str,
    sender_role: str,
    body: str,
    created_at: Any = None,
) -> None:
    """Indexa fuera del request: la persistencia no depende del proveedor de embeddings."""
    threading.Thread(
        target=index_conversation_message,
        args=(message_id, trip_id, channel, sender_role, body, created_at),
        daemon=True,
    ).start()


def schedule_operational_event(
    event_key: str,
    trip_id: int,
    actor_role: str,
    event_type: str,
    previous_status: str | None,
    new_status: str | None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Proyecta eventos de auditoría a embeddings sin bloquear la transacción."""
    safe_metadata = _redact(metadata or {})
    text = (
        "FUENTE: auditoría operativa de Livre\n"
        f"EVENTO: {event_type}\nVIAJE_ID: {trip_id}\n"
        f"ROL: {actor_role}\nESTADO_ANTERIOR: {previous_status or '—'}\n"
        f"ESTADO_NUEVO: {new_status or '—'}\n"
        f"DETALLE: {json.dumps(safe_metadata, ensure_ascii=False, sort_keys=True, default=_json_default)}"
    )
    def run() -> None:
        try:
            _upsert_chunk(f"operation.event:{event_key}", 0, text[:MAX_CHARS], 1)
        except Exception:
            log.exception("No se pudo indexar evento operativo event_key=%s", event_key)
    threading.Thread(target=run, daemon=True).start()


def sync_trip_documents() -> dict[str, int]:
    """Construye un documento semántico por viaje con sus relaciones operativas."""
    with get_cursor() as cur:
        cur.execute("SELECT id, row_to_json(t)::text AS trip_json FROM livi_trips t ORDER BY id")
        trips = cur.fetchall()
    indexed = 0
    for trip in trips:
        trip_id = trip["id"]
        with get_cursor() as cur:
            cur.execute("""
                SELECT created_at, sender_role, channel, body
                FROM livre_trip_messages
                WHERE trip_id = %s
                ORDER BY created_at, id
            """, [trip_id])
            messages = cur.fetchall()
            cur.execute("""
                SELECT created_at, actor_role, event_type, previous_status,
                       new_status, metadata
                FROM livre_trip_audit_events
                WHERE trip_id = %s
                ORDER BY created_at, id
            """, [trip_id])
            events = cur.fetchall()
            cur.execute("""
                SELECT row_to_json(p)::text AS payment_json
                FROM livi_payments p
                WHERE p.trip_id = %s
                ORDER BY p.created_at, p.id
            """, [trip_id])
            payments = cur.fetchall()
        message_lines = [
            f"{m['created_at']} | {m['channel']} | {m['sender_role']}: {(m['body'] or '[audio/media sin transcripción]')[:MAX_CHARS]}"
            for m in messages
        ]
        event_lines = [
            json.dumps(_redact(dict(e)), ensure_ascii=False, default=_json_default)
            for e in events
        ]
        payment_lines = [
            json.dumps(_redact(json.loads(p['payment_json'])), ensure_ascii=False, sort_keys=True, default=_json_default)
            for p in payments
        ]
        content = (
            "FUENTE: ficha operativa canónica de Livre\n"
            f"VIAJE_ID: {trip_id}\n"
            f"DATOS_DEL_VIAJE: {trip['trip_json']}\n"
            f"PAGOS: {'; '.join(payment_lines) or 'sin_registros'}\n"
            f"CONVERSACION: {' || '.join(message_lines) or 'sin_mensajes'}\n"
            f"AUDITORIA: {' || '.join(event_lines) or 'sin_eventos'}"
        )
        _upsert_chunk(f"trip:{trip_id}", 0, content[:MAX_CHARS * 4], 1)
        indexed += 1
    return {"trips_seen": len(trips), "trips_indexed": indexed}


def sync_conversation_knowledge() -> dict[str, int]:
    """Backfill seguro: indexa texto histórico, nunca media_data."""
    with get_cursor() as cur:
        cur.execute("""
            SELECT id, trip_id, channel, sender_role, body, created_at
            FROM livre_trip_messages
            WHERE body IS NOT NULL AND btrim(body) <> ''
            ORDER BY id ASC
        """)
        rows = cur.fetchall()
    indexed = 0
    for row in rows:
        index_conversation_message(row["id"], row["trip_id"], row["channel"], row["sender_role"], row["body"], row["created_at"])
        indexed += 1
    return {"messages_seen": len(rows), "messages_indexed": indexed}


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
