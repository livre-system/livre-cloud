import os
import httpx
import logging
import hashlib
from app.core.database import get_cursor

log = logging.getLogger(__name__)

ELEVENLABS_API_URL = "https://api.elevenlabs.io/v1/convai/whatsapp/outbound-message"
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_AGENT_ID = os.getenv("ELEVENLABS_AGENT_ID", "agent_3701m0dfvnmmemptqcm2jym9wdvv")
ELEVENLABS_WHATSAPP_PHONE_NUMBER_ID = os.getenv("ELEVENLABS_WHATSAPP_PHONE_NUMBER_ID", "1364913613361192")
TEMPLATE_NAME = os.getenv("ELEVENLABS_WHATSAPP_ADMIN_NOTICE_TEMPLATE_NAME", "").strip()

def send_arrival_whatsapp(trip_id: int):
    """Envía WhatsApp de aviso de llegada usando plantilla administrativa."""
    if not TEMPLATE_NAME:
        log.warning("[ELEVEN ADMIN] Plantilla no configurada, omitiendo envío para trip=%s", trip_id)
        return

    with get_cursor() as cur:
        # Intentar obtener datos del CRM, pero ser flexibles
        cur.execute("""
            SELECT t.whatsapp_user, p.first_name, t.tracking_plate 
            FROM livi_trips t
            LEFT JOIN livre_crm_passengers p ON t.whatsapp_user = p.phone
            WHERE t.id = %s
        """, [trip_id])
        trip = cur.fetchone()
        
    if not trip or not trip.get("whatsapp_user"):
        log.error("[ELEVEN ADMIN] Pasajero no encontrado o sin ID para trip=%s", trip_id)
        return

    # Usar valores por defecto seguros si falta info del CRM
    first_name = trip.get("first_name") or "Pasajero"
    plate = trip.get("tracking_plate") or "tu conductor"

    # Idempotencia: Verificar si ya se envió este aviso de llegada para este trip
    with get_cursor() as cur:
        cur.execute("SELECT id FROM livre_trip_event_outbox WHERE trip_id = %s AND event_type = 'arrival_notice_sent' AND status = 'sent'", [trip_id])
        if cur.fetchone():
            log.info("[ELEVEN ADMIN] Aviso de llegada ya enviado, trip=%s, omitiendo.", trip_id)
            return

    # Preparar envío
    payload = {
        "phone_number_id": ELEVENLABS_WHATSAPP_PHONE_NUMBER_ID,
        "to": trip["whatsapp_user"],
        "template_name": TEMPLATE_NAME,
        "template_params": [
            {"type": "body", "parameters": [{"text": first_name}, {"text": plate}]}
        ]
    }

    try:
        response = httpx.post(
            ELEVENLABS_API_URL,
            json=payload,
            headers={"xi-api-key": ELEVENLABS_API_KEY},
            timeout=10.0
        )
        response.raise_for_status()
        
        # Registrar éxito en outbox
        with get_cursor() as cur:
            cur.execute("""
                INSERT INTO livre_trip_event_outbox 
                (trip_id, event_type, status, created_at, processing_at)
                VALUES (%s, 'arrival_notice_sent', 'sent', NOW(), NOW())
            """, [trip_id])
        
        log.info("[ELEVEN ADMIN] Aviso de llegada enviado, trip=%s", trip_id)
    except Exception as e:
        # Registrar fallo
        with get_cursor() as cur:
             cur.execute("""
                INSERT INTO livre_trip_event_outbox 
                (trip_id, event_type, status, last_error, created_at)
                VALUES (%s, 'arrival_notice_sent', 'failed', %s, NOW())
            """, [trip_id, str(e)[:255]])
        log.error("[ELEVEN ADMIN] Fallo en envío, trip=%s, error=%s", trip_id, str(e))

