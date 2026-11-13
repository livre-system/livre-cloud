import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from app.core.database import get_cursor

router = APIRouter(prefix="/webhooks/meta", tags=["meta-webhooks"])
log = logging.getLogger("uvicorn.error")
VERIFY_TOKEN = os.getenv("META_WEBHOOK_VERIFY_TOKEN", "")
APP_SECRET = os.getenv("META_APP_SECRET", "")


def _verify_signature(raw: bytes, header: str) -> bool:
    if not APP_SECRET:
        return False
    expected = "sha256=" + hmac.new(APP_SECRET.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header or "")


@router.get("")
async def verify_meta_webhook(request: Request):
    query = request.query_params
    if not VERIFY_TOKEN or query.get("hub.verify_token") != VERIFY_TOKEN:
        raise HTTPException(status_code=403, detail="Token de verificación inválido")
    return int(query.get("hub.challenge", "0"))


@router.post("")
async def receive_meta_webhook(request: Request):
    raw = await request.body()
    if not _verify_signature(raw, request.headers.get("x-hub-signature-256", "")):
        raise HTTPException(status_code=403, detail="Firma Meta inválida")
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="JSON inválido") from exc

    saved = 0
    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value") or {}
            for status in value.get("statuses", []):
                wamid = str(status.get("id") or "").strip()
                state = str(status.get("status") or "").strip().lower()
                recipient = str(status.get("recipient_id") or status.get("recipient_user_id") or "").strip()
                if not wamid or not state:
                    continue
                errors = status.get("errors") or []
                with get_cursor() as cur:
                    cur.execute(
                        """INSERT INTO livre_whatsapp_message_events
                           (wamid, recipient_id, status, status_timestamp, error_code, error_message, payload)
                           VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                           ON CONFLICT (wamid) DO UPDATE SET status = EXCLUDED.status,
                             status_timestamp = EXCLUDED.status_timestamp,
                             error_code = EXCLUDED.error_code,
                             error_message = EXCLUDED.error_message,
                             payload = EXCLUDED.payload""",
                        (
                            wamid, recipient, state,
                            datetime.fromtimestamp(int(status["timestamp"]), tz=timezone.utc) if status.get("timestamp") else None,
                            str(errors[0].get("code")) if errors else None,
                            errors[0].get("message") if errors else None,
                            json.dumps(status),
                        ),
                    )
                saved += 1
                log.info("[META TRACKING] wamid=%s recipient=%s status=%s error=%s", wamid, recipient, state, errors[0].get("code") if errors else "none")
    return {"ok": True, "statuses_saved": saved}
