"""Despacho idempotente de eventos operativos fuera de la transacción web."""
from __future__ import annotations

import logging
import time
from typing import Any

import requests

from app.core.config import OUTBOX_WEBHOOK_URL
from app.core.database import get_conn

log = logging.getLogger(__name__)


def claim_batch(limit: int = 25) -> list[dict[str, Any]]:
    """Toma eventos disponibles con bloqueo corto y los marca processing."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                WITH claimed AS (
                    SELECT id FROM livre_trip_event_outbox
                    WHERE (status IN ('pending', 'failed') AND available_at <= now())
                       OR (status = 'processing' AND processing_at < now() - interval '5 minutes')
                    ORDER BY id
                    FOR UPDATE SKIP LOCKED
                    LIMIT %s
                )
                UPDATE livre_trip_event_outbox o
                   SET status = 'processing', processing_at = now(), attempts = attempts + 1
                  FROM claimed
                 WHERE o.id = claimed.id
                RETURNING o.id, o.event_key, o.event_type, o.payload, o.attempts
                """,
                [limit],
            )
            return [dict(row) for row in cur.fetchall()]


def mark_sent(event_id: int) -> None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE livre_trip_event_outbox
                   SET status = 'sent', sent_at = now(), processing_at = NULL,
                       last_error = NULL
                 WHERE id = %s AND status = 'processing'""",
                [event_id],
            )


def mark_failed(event_id: int, error: str, attempts: int) -> None:
    delay_seconds = min(3600, 2 ** min(max(attempts, 1), 10))
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE livre_trip_event_outbox
                   SET status = 'failed', processing_at = NULL, last_error = %s,
                       available_at = now() + (%s * interval '1 second')
                 WHERE id = %s AND status = 'processing'""",
                [error[:1000], delay_seconds, event_id],
            )


def dispatch_event(event: dict[str, Any]) -> None:
    """Entrega al sink configurado; no marca éxito si no hay sink."""
    if not OUTBOX_WEBHOOK_URL:
        raise RuntimeError("OUTBOX_WEBHOOK_URL no está configurada")
    response = requests.post(
        OUTBOX_WEBHOOK_URL,
        json={
            "event_key": event["event_key"],
            "event_type": event["event_type"],
            "payload": event["payload"],
        },
        timeout=10,
    )
    response.raise_for_status()


def run_once(limit: int = 25) -> dict[str, int]:
    events = claim_batch(limit)
    sent = failed = 0
    for event in events:
        try:
            dispatch_event(event)
        except Exception as exc:  # noqa: BLE001 - worker must continue the batch
            failed += 1
            log.warning("outbox event %s failed: %s", event["id"], exc)
            mark_failed(event["id"], str(exc), event["attempts"])
        else:
            sent += 1
            mark_sent(event["id"])
    return {"claimed": len(events), "sent": sent, "failed": failed}


def run_forever(interval: float = 5.0, limit: int = 25) -> None:
    while True:
        run_once(limit)
        time.sleep(interval)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_forever()
