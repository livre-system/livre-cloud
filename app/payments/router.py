"""Pagos anticipados de Livre Ahorro.

Mercado Pago es un proveedor detrás de Livre Cloud. WhatsApp y el pasajero
nunca hablan directamente con el proveedor ni reciben credenciales.
"""

import json
import logging
import os
import uuid
from decimal import Decimal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.database import get_cursor
from app.core.jwt import require_auth
from app.integrations.historical_quote import apply_commercial_discount
from app.voice.elevenlabs_trip import (
    _get_whatsapp_quote,
    _save_trip,
    _save_whatsapp_quote,
    _run_local_quote,
    _tracking_url,
)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/payments", tags=["payments"])
whatsapp_router = APIRouter(prefix="/elevenlabs", tags=["elevenlabs-payments"])

MERCADOPAGO_API = "https://api.mercadopago.com"
PASSENGER_PAYMENT_URL = os.getenv(
    "PASSENGER_PAYMENT_URL",
    "https://livre-pasajeros-production.up.railway.app/payment.html",
).strip()
PUBLIC_API_URL = os.getenv("PUBLIC_API_URL", "https://livre-cloud-production.up.railway.app").rstrip("/")


def _access_token() -> str:
    token = os.getenv("MERCADOPAGO_ACCESS_TOKEN", "").strip()
    if not token:
        raise HTTPException(status_code=503, detail="Mercado Pago todavía no está configurado")
    return token


def _provider_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_access_token()}", "Content-Type": "application/json"}


def _quote_option(quote: dict, service_type_id: str) -> dict:
    return next(
        (item for item in (quote.get("options") or []) if str(item.get("transportTypeId")) == service_type_id),
        None,
    )


def _reference() -> str:
    return f"livre_payment:{uuid.uuid4()}"


def _payment_row(payment_id: int | None = None, reference: str | None = None) -> dict | None:
    with get_cursor() as cur:
        if payment_id is not None:
            cur.execute("SELECT * FROM livi_payments WHERE id = %s", (payment_id,))
        else:
            cur.execute("SELECT * FROM livi_payments WHERE external_reference = %s", (reference,))
        return cur.fetchone()


def _complete_payment(row: dict) -> dict:
    if row["status"] != "approved":
        raise HTTPException(status_code=409, detail="El pago todavía no está aprobado")
    if row.get("trip_id"):
        return {"trip_id": row["trip_id"], "tracking_url": _tracking_url(row.get("tracking_token")), "status": "approved"}

    source = row.get("source") or "whatsapp"
    quote = _get_whatsapp_quote(row["quote_id"], row["conversation_id"], row["whatsapp_user"], source=source)
    if not quote:
        raise HTTPException(status_code=409, detail="La cotización venció o ya fue utilizada")
    selected = _quote_option(quote, str(row["service_type_id"]))
    if not selected:
        raise HTTPException(status_code=409, detail="El servicio ya no coincide con la cotización")
    amount = Decimal(str(selected.get("pricePointToPoint", selected.get("price"))))
    if amount != Decimal(str(row["amount"])):
        raise HTTPException(status_code=409, detail="El importe pagado no coincide con la cotización")

    financials = apply_commercial_discount(selected)
    trip_id, tracking_token = _save_trip(
        conversation_id=row["conversation_id"],
        origen=quote["origen"],
        destino=quote["destino"],
        whatsapp_user=row["whatsapp_user"],
        user_id=row.get("user_id"),
        service_type_id=str(row["service_type_id"]),
        service_type_name="Livre Ahorro",
        quote_id=row["quote_id"],
        fare=Decimal(str(financials["originalFare"])),
        original_fare=Decimal(str(financials["originalFare"])),
        discount_percentage=Decimal(str(financials["discountPercentage"])),
        discount_amount=Decimal(str(financials["discountAmount"])),
        final_fare=Decimal(str(financials["finalFare"])),
        fare_source="official",
        source=source,
        status="searching",
    )
    with get_cursor() as cur:
        cur.execute(
            "UPDATE livi_whatsapp_quotes SET consumed_at = NOW() WHERE quote_id = %s AND consumed_at IS NULL",
            (row["quote_id"],),
        )
        cur.execute(
            "UPDATE livi_payments SET trip_id = %s, tracking_token = %s, completed_at = NOW() WHERE id = %s AND trip_id IS NULL RETURNING id",
            (trip_id, tracking_token, row["id"]),
        )
    return {"trip_id": trip_id, "tracking_url": _tracking_url(tracking_token), "status": "approved"}


@router.post("/mercadopago/create")
def create_mercadopago_link(payload: dict):
    """Crea un link de pago para una cotización Ahorro aún no consumida."""
    if str(payload.get("service_type_id")) != "2":
        raise HTTPException(status_code=422, detail="Sólo Livre Ahorro requiere pago anticipado")
    quote_id = str(payload.get("quote_id") or "").strip()
    conversation_id = str(payload.get("conversation_id") or "").strip()
    whatsapp_user = str(payload.get("whatsapp_user") or "").strip()
    if not quote_id or not conversation_id or not whatsapp_user:
        raise HTTPException(status_code=422, detail="Faltan datos de la cotización o del pasajero")
    source = str(payload.get("source") or "whatsapp")
    user_id = str(payload.get("user_id") or "").strip() or None
    if source not in {"whatsapp", "passenger"}:
        raise HTTPException(status_code=422, detail="Origen de pago inválido")
    _access_token()
    quote = _get_whatsapp_quote(quote_id, conversation_id, whatsapp_user, source=source)
    if not quote:
        raise HTTPException(status_code=404, detail="Cotización no encontrada, vencida o ya utilizada")
    selected = _quote_option(quote, "2")
    if not selected:
        raise HTTPException(status_code=409, detail="La cotización no contiene Livre Ahorro")
    amount = Decimal(str(selected.get("pricePointToPoint", selected.get("price"))))
    with get_cursor() as cur:
        cur.execute(
            """INSERT INTO livi_payments
               (provider, source, user_id, quote_id, conversation_id, whatsapp_user, service_type_id, amount, status, external_reference)
               VALUES ('mercadopago', %s, %s, %s, %s, %s, 2, %s, 'created', '') RETURNING id""",
            (source, user_id, quote_id, conversation_id, whatsapp_user, amount),
        )
        payment_id = cur.fetchone()["id"]
        reference = _reference()
        cur.execute("UPDATE livi_payments SET external_reference = %s WHERE id = %s", (reference, payment_id))

    preference = {
        "items": [{"title": "Livre Ahorro", "quantity": 1, "currency_id": "ARS", "unit_price": float(amount)}],
        "external_reference": reference,
        "notification_url": f"{PUBLIC_API_URL}/payments/mercadopago/webhook",
        "back_urls": {"success": f"{PASSENGER_PAYMENT_URL}?reference={reference}", "pending": f"{PASSENGER_PAYMENT_URL}?reference={reference}", "failure": f"{PASSENGER_PAYMENT_URL}?reference={reference}"},
        "auto_return": "approved",
        "metadata": {"payment_id": payment_id, "quote_id": quote_id, "conversation_id": conversation_id, "whatsapp_user": whatsapp_user, "service_type_id": 2},
    }
    try:
        response = httpx.post(f"{MERCADOPAGO_API}/checkout/preferences", headers=_provider_headers(), json=preference, timeout=20.0)
        response.raise_for_status()
        provider = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        with get_cursor() as cur:
            cur.execute("UPDATE livi_payments SET status = 'failed', provider_response = %s::jsonb WHERE id = %s", (json.dumps({"error": str(exc)}), payment_id))
        raise HTTPException(status_code=502, detail="Mercado Pago no pudo crear el link") from exc
    init_point = provider.get("init_point") or provider.get("sandbox_init_point")
    if not init_point:
        raise HTTPException(status_code=502, detail="Mercado Pago no devolvió un link de pago")
    with get_cursor() as cur:
        cur.execute("UPDATE livi_payments SET provider_preference_id = %s, init_point = %s, provider_response = %s::jsonb WHERE id = %s", (provider.get("id"), init_point, json.dumps(provider), payment_id))
    return {"payment_id": payment_id, "reference": reference, "payment_url": init_point, "amount": float(amount), "currency": "ARS", "service_type_id": 2}


@router.post("/passenger/create")
def passenger_payment_link(payload: dict, user: dict = Depends(require_auth)):
    """Crea el pago Ahorro desde la app pasajero sin crear aún el viaje."""
    origin = str(payload.get("origin") or "").strip()
    destination = str(payload.get("destination") or "").strip()
    if not origin or not destination:
        raise HTTPException(status_code=422, detail="Faltan origen o destino")
    quote = _run_local_quote(origin, destination)
    if quote.get("error"):
        raise HTTPException(status_code=422, detail="No pudimos calcular el viaje")
    options = [item for item in quote.get("options", []) if str(item.get("transportTypeId")) in {"1", "2"}]
    conversation_id = f"web_{uuid.uuid4()}"
    passenger_key = f"user:{user['id']}"
    quote_id = _save_whatsapp_quote(
        conversation_id=conversation_id,
        whatsapp_user=passenger_key,
        origen=origin,
        destino=destination,
        options=options,
        source="passenger",
    )
    return create_mercadopago_link({
        "service_type_id": "2",
        "quote_id": quote_id,
        "conversation_id": conversation_id,
        "whatsapp_user": passenger_key,
        "source": "passenger",
        "user_id": user["id"],
    })


@whatsapp_router.post("/payment-link")
def whatsapp_payment_link(payload: dict):
    """Tool de WhatsApp: devuelve el link para pagar Livre Ahorro."""
    result = create_mercadopago_link(payload)
    return {
        "result": f"Link de pago de Livre Ahorro: {result['payment_url']}. Una vez aprobado, se confirma el viaje y se entrega el seguimiento.",
        **result,
    }


@router.post("/mercadopago/webhook")
def mercadopago_webhook(request: Request, payload: dict):
    """Lee el estado real en Mercado Pago y actualiza el pago local."""
    data = payload.get("data") or {}
    provider_payment_id = str(data.get("id") or payload.get("id") or "").strip()
    if not provider_payment_id:
        return {"received": True, "ignored": "missing_payment_id"}
    try:
        response = httpx.get(f"{MERCADOPAGO_API}/v1/payments/{provider_payment_id}", headers=_provider_headers(), timeout=20.0)
        response.raise_for_status()
        provider = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("Mercado Pago webhook read-back failed payment=%s error=%s", provider_payment_id, exc)
        raise HTTPException(status_code=502, detail="No se pudo verificar el pago en Mercado Pago") from exc
    reference = provider.get("external_reference")
    status = str(provider.get("status") or "unknown")
    payment_method = str(provider.get("payment_method_id") or "").strip() or None
    payment_type = str(provider.get("payment_type_id") or "").strip() or None
    paid_at = provider.get("date_approved") or provider.get("date_last_updated")
    with get_cursor() as cur:
        cur.execute("""UPDATE livi_payments
            SET status = %s, payment_method = %s, payment_type = %s, paid_at = %s,
                provider_payment_id = %s, provider_response = %s::jsonb, updated_at = NOW()
            WHERE external_reference = %s RETURNING id""", (status, payment_method, payment_type, paid_at, provider_payment_id, json.dumps(provider), reference))
        row = cur.fetchone()
    return {"received": True, "payment_id": row["id"] if row else None, "status": status}


@router.get("/status")
def payment_status(reference: str):
    row = _payment_row(reference=reference)
    if not row:
        raise HTTPException(status_code=404, detail="Pago no encontrado")
    result = {"reference": reference, "status": row["status"], "amount": float(row["amount"]), "currency": "ARS", "tracking_url": _tracking_url(row.get("tracking_token"))}
    return result


@router.post("/complete")
def complete_payment(payload: dict):
    reference = str(payload.get("reference") or "").strip()
    row = _payment_row(reference=reference)
    if not row:
        raise HTTPException(status_code=404, detail="Pago no encontrado")
    return _complete_payment(row)
