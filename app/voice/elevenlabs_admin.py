import os
import httpx
import logging
from app.core.database import get_cursor

log = logging.getLogger(__name__)

ELEVENLABS_API_URL = "https://api.elevenlabs.io/v1/convai/whatsapp/outbound-message"
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_AGENT_ID = os.getenv("ELEVENLABS_AGENT_ID", "agent_3701m0dfvnmmemptqcm2jym9wdvv")
ELEVENLABS_WHATSAPP_PHONE_NUMBER_ID = os.getenv("ELEVENLABS_WHATSAPP_PHONE_NUMBER_ID", "1364913613361192")
ELEVENLABS_WHATSAPP_TEMPLATE_LANGUAGE = os.getenv("ELEVENLABS_WHATSAPP_TEMPLATE_LANGUAGE", "es_AR").strip() or "es_AR"
TEMPLATE_NAME = os.getenv("ELEVENLABS_WHATSAPP_ADMIN_NOTICE_TEMPLATE_NAME", "livre_admin_notice").strip() or "livre_admin_notice"
log.info("[ELEVEN ADMIN] Configuración de plantilla: %s", TEMPLATE_NAME)

def send_arrival_whatsapp(trip_id: int, cur=None):
    """Envía WhatsApp de aviso de llegada usando plantilla administrativa."""
    if not TEMPLATE_NAME:
        log.warning("[ELEVEN ADMIN] Plantilla no configurada, omitiendo envío para trip=%s", trip_id)
        return

    # Usar cursor proporcionado o nuevo
    my_cur = cur if cur else get_cursor()
    
    # Obtener datos de forma directa y plana
    my_cur.execute("""
        SELECT t.whatsapp_user, p.first_name, t.tracking_plate 
        FROM livi_trips t
        LEFT JOIN livre_crm_passengers p ON t.whatsapp_user = p.phone
        WHERE t.id = %s
    """, [trip_id])
    trip = my_cur.fetchone()
    # Log de diagnóstico: ¿Qué encontramos?
    log.info("[DIAGNOSTICO LLEGADA] trip=%s found=%s whatsapp_user=%r", trip_id, bool(trip), trip.get("whatsapp_user") if trip else "N/A")
        
    if not trip or not trip.get("whatsapp_user"):
        log.error("[ELEVEN ADMIN] Pasajero no encontrado o sin ID para trip=%s", trip_id)
        return

    # Valores directos
    first_name = trip.get("first_name") or "Pasajero"
    plate = trip.get("tracking_plate") or "tu conductor"

    # Idempotencia
    my_cur.execute("SELECT id FROM livre_trip_event_outbox WHERE trip_id = %s AND event_type = 'arrival_notice_sent' AND status = 'sent'", [trip_id])
    if my_cur.fetchone():
        return

    payload = {
        "whatsapp_phone_number_id": ELEVENLABS_WHATSAPP_PHONE_NUMBER_ID,
        "whatsapp_user_id": trip["whatsapp_user"],
        "template_name": TEMPLATE_NAME,
        "template_language_code": ELEVENLABS_WHATSAPP_TEMPLATE_LANGUAGE,
        "template_params": [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": first_name},
                    {"type": "text", "text": plate},
                ],
            }
        ],
        "agent_id": ELEVENLABS_AGENT_ID,
    }

    try:
        response = httpx.post(
            ELEVENLABS_API_URL,
            json=payload,
            headers={"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"},
            timeout=10.0
        )
        if response.is_error:
            log.error("[ELEVEN ADMIN] proveedor rechazó trip=%s status=%s body=%s", trip_id, response.status_code, response.text[:800])
            response.raise_for_status()
        result = response.json()
        conversation_id = result.get("conversation_id") or ""
        log.info("[ELEVEN ADMIN] proveedor aceptó trip=%s conversation_id_present=%s", trip_id, bool(conversation_id))
        my_cur.execute("""
            INSERT INTO livre_trip_event_outbox
            (trip_id, event_type, status, created_at, processing_at)
            VALUES (%s, 'arrival_notice_sent', 'sent', NOW(), NOW())
        """, [trip_id])
        log.info("[ELEVEN ADMIN] Aviso de llegada aceptado, trip=%s", trip_id)
    except Exception as e:
        log.error("[ELEVEN ADMIN] Fallo en envío, trip=%s, error=%s", trip_id, str(e))

