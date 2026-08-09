"""
paypal_webhook.py
Recibe notificaciones de PayPal cuando se completa un pago.
Activa el plan pro del usuario automáticamente.
"""
import os
import httpx
import logging
from fastapi import APIRouter, Request, HTTPException
from app.core.database import get_cursor

log = logging.getLogger(__name__)

router = APIRouter(prefix="/payments", tags=["payments"])

PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID")
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET")
PAYPAL_API = "https://api-m.paypal.com"  # producción


async def get_paypal_token() -> str:
    async with httpx.AsyncClient() as client:
        res = await client.post(
            f"{PAYPAL_API}/v1/oauth2/token",
            data={"grant_type": "client_credentials"},
            auth=(PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET),
        )
        res.raise_for_status()
        return res.json()["access_token"]


async def verify_webhook(headers: dict, body: bytes, webhook_id: str) -> bool:
    """Verifica que el webhook realmente viene de PayPal."""
    try:
        token = await get_paypal_token()
        async with httpx.AsyncClient() as client:
            res = await client.post(
                f"{PAYPAL_API}/v1/notifications/verify-webhook-signature",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "auth_algo":         headers.get("paypal-auth-algo", ""),
                    "cert_url":          headers.get("paypal-cert-url", ""),
                    "transmission_id":   headers.get("paypal-transmission-id", ""),
                    "transmission_sig":  headers.get("paypal-transmission-sig", ""),
                    "transmission_time": headers.get("paypal-transmission-time", ""),
                    "webhook_id":        webhook_id,
                    "webhook_event":     body.decode("utf-8"),
                },
            )
            data = res.json()
            return data.get("verification_status") == "SUCCESS"
    except Exception as e:
        log.error(f"verify_webhook error: {e}")
        return False


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


@router.post("/paypal/webhook")
async def paypal_webhook(request: Request):
    body = await request.body()
    headers = dict(request.headers)

    # ID del webhook — lo configurás en el dashboard de PayPal
    webhook_id = os.getenv("PAYPAL_WEBHOOK_ID", "")

    if webhook_id:
        valid = await verify_webhook(headers, body, webhook_id)
        if not valid:
            log.warning("Webhook de PayPal no verificado")
            raise HTTPException(status_code=400, detail="Webhook inválido")

    import json
    event = json.loads(body)
    event_type = event.get("event_type", "")
    log.info(f"PayPal webhook recibido: {event_type}")

    # Solo procesar pagos completados
    if event_type in ("PAYMENT.CAPTURE.COMPLETED", "CHECKOUT.ORDER.APPROVED"):
        resource = event.get("resource", {})

        # Intentar sacar el email del pagador
        payer = resource.get("payer", {})
        email = payer.get("email_address", "")

        if not email:
            # Buscar en payment_pending como fallback
            with get_cursor() as cur:
                cur.execute("SELECT email FROM payment_pending ORDER BY created_at DESC LIMIT 1")
                row = cur.fetchone()
                if row:
                    email = row["email"]

        if email:
            user_id = find_user_by_email(email)
            if user_id:
                activate_plan(user_id)
                # Limpiar pendiente si existe
                with get_cursor() as cur:
                    cur.execute("DELETE FROM payment_pending WHERE email = %s", [email])
            else:
                log.warning(f"PayPal: email {email} no encontrado en DB")
        else:
            log.warning("PayPal: no se pudo identificar el email del pagador")

    return {"status": "ok"}
