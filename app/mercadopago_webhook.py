"""
mercadopago_webhook.py
Recibe notificaciones de Mercado Pago cuando se completa un pago.
Activa el plan pro del usuario automáticamente.
"""
import os
import httpx
import logging
from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
from app.core.database import get_cursor

log = logging.getLogger(__name__)

router = APIRouter(prefix="/payments", tags=["payments"])

MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN")


def activate_plan(user_id: str):
    with get_cursor() as cur:
        cur.execute("""
            UPDATE memory_users
            SET plan = 'pro', plan_activated_at = NOW()
            WHERE user_id = %s
        """, [user_id])
    log.info(f"Plan pro activado para {user_id}")


def find_user_by_email(email: str) -> str | None:
    try:
        with get_cursor() as cur:
            cur.execute("SELECT user_id FROM memory_users WHERE email = %s", [email])
            row = cur.fetchone()
            return row["user_id"] if row else None
    except Exception:
        return None


def get_pending_email() -> str | None:
    try:
        with get_cursor() as cur:
            cur.execute("SELECT email FROM payment_pending ORDER BY created_at DESC LIMIT 1")
            row = cur.fetchone()
            return row["email"] if row else None
    except Exception:
        return None


@router.post("/mercadopago/webhook")
async def mercadopago_webhook(request: Request):
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON inválido")

    topic = data.get("type") or request.query_params.get("topic", "")
    resource_id = data.get("data", {}).get("id") or request.query_params.get("id", "")

    log.info(f"MP webhook recibido: type={topic} id={resource_id}")

    if topic not in ("payment",):
        return {"status": "ignored"}

    if not resource_id:
        return {"status": "no id"}

    try:
        async with httpx.AsyncClient() as client:
            res = await client.get(
                f"https://api.mercadopago.com/v1/payments/{resource_id}",
                headers={"Authorization": f"Bearer {MP_ACCESS_TOKEN}"},
            )
            payment = res.json()
    except Exception as e:
        log.error(f"MP API error: {e}")
        raise HTTPException(status_code=500, detail="Error consultando pago")

    status = payment.get("status")
    log.info(f"MP pago {resource_id} status={status}")

    if status != "approved":
        return {"status": "not approved"}

    email = payment.get("payer", {}).get("email", "")
    if not email:
        email = get_pending_email() or ""

    if not email:
        log.warning("MP: no se pudo identificar el email del pagador")
        return {"status": "no email"}

    user_id = find_user_by_email(email)
    if user_id:
        activate_plan(user_id)
        with get_cursor() as cur:
            cur.execute("DELETE FROM payment_pending WHERE email = %s", [email])
        log.info(f"MP: plan activado para {email}")
    else:
        log.warning(f"MP: email {email} no encontrado en DB")

    return {"status": "ok"}


class MPCreateRequest(BaseModel):
    amount: float = 10
    description: str = "HASH Pro"


@router.post("/mercadopago/create")
async def mp_create_preference(body: MPCreateRequest):
    try:
        async with httpx.AsyncClient() as client:
            res = await client.post(
                "https://api.mercadopago.com/checkout/preferences",
                headers={
                    "Authorization": f"Bearer {MP_ACCESS_TOKEN}",
                    "Content-Type": "application/json",
                },
                json={
                    "items": [{
                        "title": body.description,
                        "quantity": 1,
                        "currency_id": "USD",
                        "unit_price": body.amount,
                    }],
                    "back_urls": {
                        "success": "https://hash-ia.site",
                        "failure": "https://hash-ia.site",
                    },
                    "notification_url": "https://hash-cloud-production.up.railway.app/payments/mercadopago/webhook",
                },
            )
            data = res.json()
            return {"init_point": data.get("init_point")}
    except Exception as e:
        log.error(f"MP create preference error: {e}")
        raise HTTPException(status_code=500, detail="Error creando preferencia")
