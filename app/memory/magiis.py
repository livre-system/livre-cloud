"""Proyección de la operación MAGIIS en la memoria semántica del usuario actual.

La memoria sigue siendo la única fuente que consulta la IA. Esta capa convierte
los registros normalizados del CRM en documentos legibles y embebibles, sin
modificar MAGIIS ni mezclar datos con la memoria del cerebro.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from app.core.config import MEMORY_USER_ID
from app.core.database import get_cursor
from app.memory.repository import (
    add_row,
    create_user,
    delete_document,
    get_or_create_document,
)

log = logging.getLogger(__name__)

MAX_CHARS = 6000


def _json_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def _chunks(records: list[str], *, max_chars: int = MAX_CHARS) -> list[str]:
    """Agrupa registros completos sin cortar una entidad por la mitad."""
    result: list[str] = []
    current: list[str] = []
    size = 0
    for record in records:
        addition = len(record) + (2 if current else 0)
        if current and size + addition > max_chars:
            result.append("\n\n".join(current))
            current = []
            size = 0
        current.append(record)
        size += len(record) + (2 if len(current) > 1 else 0)
    if current:
        result.append("\n\n".join(current))
    return result


def _read_table(table: str) -> list[dict[str, Any]]:
    # Los nombres de tabla son constantes internas, nunca vienen del usuario.
    with get_cursor() as cur:
        cur.execute(
            f"SELECT magiis_id, raw_payload, first_seen_at, last_seen_at "
            f"FROM {table} ORDER BY magiis_id"
        )
        return list(cur.fetchall())


def _travel_rows() -> list[dict[str, Any]]:
    with get_cursor() as cur:
        cur.execute(
            """
            SELECT magiis_id, raw_payload, travel_date, state,
                   passenger_magiis_id, driver_magiis_id,
                   first_seen_at, last_seen_at
            FROM magiis_crm_travels
            ORDER BY travel_date NULLS LAST, magiis_id
            """
        )
        return list(cur.fetchall())


def _record_text(kind: str, row: dict[str, Any]) -> str:
    payload = row.get("raw_payload") or {}
    fields = {
        "tipo": kind,
        "magiis_id": row.get("magiis_id"),
        "datos": payload,
        "visto_por_primera_vez": row.get("first_seen_at"),
        "actualizado_en": row.get("last_seen_at"),
    }
    if kind == "viaje":
        fields.update(
            {
                "fecha_viaje": row.get("travel_date"),
                "estado": row.get("state"),
                "pasajero_magiis_id": row.get("passenger_magiis_id"),
                "conductor_magiis_id": row.get("driver_magiis_id"),
            }
        )
    return _json_value(fields)


def _replace_document(user_id: str, key: str, name: str, description: str, chunks: list[str]) -> int:
    """Reemplaza solo la proyección MAGIIS de un documento reservado."""
    delete_document(user_id, key)
    document_id, _ = get_or_create_document(user_id, key, name, description)
    saved = 0
    for chunk in chunks:
        if not chunk.strip():
            continue
        try:
            # El texto se conserva aunque el proveedor de embeddings falle.
            add_row(document_id, {"message": chunk}, with_embedding=True)
        except Exception:
            log.exception("Embedding falló para %s; se guarda el chunk sin embedding", key)
            from app.memory.repository import add_row_without_embedding
            add_row_without_embedding(document_id, {"message": chunk})
        saved += 1
    return saved


def sync_magiis_memory(user_id: str = MEMORY_USER_ID) -> dict[str, Any]:
    """Proyecta el último estado persistido del CRM a la memoria del usuario.

    Es deliberadamente best-effort: la sincronización de tablas ya completada
    no debe fallar porque el proveedor de embeddings esté caído.
    """
    create_user(user_id)
    started_at = datetime.now(timezone.utc).isoformat()
    counts: dict[str, int] = {}
    try:
        sources = {
            "conductores": (_read_table("magiis_crm_drivers"), "magiis_drivers", "Conductores MAGIIS"),
            "vehiculos": (_read_table("magiis_crm_vehicles"), "magiis_vehicles", "Vehículos MAGIIS"),
            "pasajeros": (_read_table("magiis_crm_passengers"), "magiis_passengers", "Pasajeros MAGIIS"),
            "viajes": (_travel_rows(), "magiis_travels", "Viajes MAGIIS"),
        }
        for kind, (rows, key, name) in sources.items():
            records = [_record_text("viaje" if kind == "viajes" else kind[:-1], row) for row in rows]
            chunks = _chunks(records)
            counts[key] = _replace_document(
                user_id,
                key,
                name,
                f"Proyección operacional de MAGIIS. Actualizada {started_at}.",
                chunks,
            )
        return {"ok": True, "user_id": user_id, "documents": counts, "updated_at": started_at}
    except Exception:
        log.exception("No se pudo proyectar MAGIIS en la memoria; se conserva el CRM persistido")
        return {"ok": False, "user_id": user_id, "documents": counts, "updated_at": started_at}
