"""Eventos operativos persistentes y sanitizados para viajes."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def record_trip_event(
    cur,
    *,
    trip_id: int,
    actor_user_id: str | None,
    actor_role: str,
    event_type: str,
    previous_status: str | None = None,
    new_status: str | None = None,
    request_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Write audit plus notification intent in the same DB transaction."""
    safe_metadata = metadata or {}
    event_key = hashlib.sha256(json.dumps(
        {"trip_id": trip_id, "event_type": event_type,
         "previous_status": previous_status, "new_status": new_status,
         "metadata": safe_metadata}, sort_keys=True, default=str,
    ).encode()).hexdigest()
    cur.execute(
        """INSERT INTO livre_trip_audit_events
           (trip_id, actor_user_id, actor_role, event_type, previous_status,
            new_status, request_id, event_key, metadata)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
           ON CONFLICT (event_key) DO NOTHING""",
        (
            trip_id,
            actor_user_id,
            actor_role,
            event_type,
            previous_status,
            new_status,
            request_id,
            event_key,
            json.dumps(safe_metadata, ensure_ascii=False, default=str),
        ),
    )
    cur.execute(
        """INSERT INTO livre_trip_event_outbox (trip_id, event_type, event_key, payload)
           VALUES (%s, %s, %s, %s::jsonb)
           ON CONFLICT (event_key) DO NOTHING""",
        (
            trip_id,
            event_type,
            event_key,
            json.dumps(
                {"trip_id": trip_id, "new_status": new_status, **safe_metadata},
                ensure_ascii=False,
                default=str,
            ),
        ),
    )
