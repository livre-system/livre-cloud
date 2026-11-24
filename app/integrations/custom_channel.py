"""ElevenLabs Custom Channel gateway for text conversations."""
import hashlib
import hmac
import json
import logging
import os
import uuid
from fastapi import APIRouter, HTTPException, Request
import httpx
from app.core.database import get_cursor

router = APIRouter(prefix="/webhooks/elevenlabs", tags=["elevenlabs-custom-channel"])
log = logging.getLogger("uvicorn.error")
TRIGGER_ID = os.getenv("ELEVENLABS_CUSTOM_CHANNEL_TRIGGER_ID", "").strip()
INBOUND_SECRET = os.getenv("ELEVENLABS_CUSTOM_CHANNEL_INBOUND_SECRET", "").strip()
OUTBOUND_SECRET = os.getenv("ELEVENLABS_CUSTOM_CHANNEL_OUTBOUND_SECRET", "").strip()
CUSTOM_URL = "https://api.elevenlabs.io/v1/convai/api-integrations/custom_channel/triggers/{}/async_message"


def _valid_signature(raw: bytes, header: str) -> bool:
    try:
        parts = dict(x.split("=", 1) for x in header.split(","))
        expected = hmac.new(OUTBOUND_SECRET.encode(), f"{parts['t']}.{raw.decode()}".encode(), hashlib.sha256).hexdigest()
        return bool(OUTBOUND_SECRET) and hmac.compare_digest(expected, parts.get("v0", ""))
    except (ValueError, KeyError, UnicodeDecodeError):
        return False


def _ensure_table(cur):
    cur.execute("""CREATE TABLE IF NOT EXISTS livre_custom_channel_sessions (
      whatsapp_user TEXT PRIMARY KEY, conversation_id TEXT, pending_text TEXT NOT NULL DEFAULT '',
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())""")


async def _send(text: str, user: str, phone: str, conversation_id: str | None = None):
    if not TRIGGER_ID or not INBOUND_SECRET:
        raise RuntimeError("Custom Channel no configurado")
    payload = {"data": {"type": "user_message", "text": text, "user_identifier": user}, "user_message_id": f"livre_{uuid.uuid4().hex}"}
    if conversation_id:
        payload["conversation_id"] = conversation_id
    else:
        payload["dynamic_variables"] = {"livre_whatsapp_phone": phone, "livre_whatsapp_user": user}
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(CUSTOM_URL.format(TRIGGER_ID), headers={"X-Webhook-Secret": INBOUND_SECRET}, json=payload)
    response.raise_for_status()
    return response.json()


@router.post("/reply")
async def custom_reply(request: Request):
    raw = await request.body()
    if not _valid_signature(raw, request.headers.get("ElevenLabs-Signature", "")):
        raise HTTPException(status_code=403, detail="Firma ElevenLabs inválida")
    body = json.loads(raw)
    conversation_id = str(body.get("conversation_id") or "")
    user_ids = body.get("user_message_ids") or []
    with get_cursor() as cur:
        _ensure_table(cur)
        row = None
        if conversation_id:
            cur.execute("SELECT * FROM livre_custom_channel_sessions WHERE conversation_id=%s", (conversation_id,))
            row = cur.fetchone()
        if row:
            if body.get("status") == "completed":
                cur.execute("UPDATE livre_custom_channel_sessions SET updated_at=NOW() WHERE conversation_id=%s", (conversation_id,))
        data = body.get("data") or []
        ended = any((x.get("type") == "agent_tool_response" and (x.get("event") or {}).get("tool_name") == "end_call" and (x.get("event") or {}).get("status") == "success") for x in data if isinstance(x, dict))
        if ended and conversation_id:
            cur.execute("DELETE FROM livre_custom_channel_sessions WHERE conversation_id=%s", (conversation_id,))
    return {"ok": True, "conversation_id": conversation_id, "user_message_ids": user_ids}


@router.post("/custom-channel/message")
async def custom_message(request: Request):
    body = await request.json()
    text = str(body.get("text") or "").strip()
    user = str(body.get("whatsapp_user") or body.get("user_identifier") or "").strip()
    phone = str(body.get("whatsapp_phone") or user).strip()
    if not text or not user:
        raise HTTPException(status_code=400, detail="text y whatsapp_user son obligatorios")
    with get_cursor() as cur:
        _ensure_table(cur)
        cur.execute("SELECT conversation_id FROM livre_custom_channel_sessions WHERE whatsapp_user=%s", (user,))
        row = cur.fetchone()
        result = await _send(text, user, phone, row["conversation_id"] if row else None)
        cid = result.get("conversation_id")
        if cid:
            cur.execute("""INSERT INTO livre_custom_channel_sessions (whatsapp_user,conversation_id,updated_at)
              VALUES (%s,%s,NOW()) ON CONFLICT (whatsapp_user) DO UPDATE SET conversation_id=EXCLUDED.conversation_id,updated_at=NOW()""", (user,cid))
    return {"ok": True, "conversation_id": cid, "status": result.get("status")}
