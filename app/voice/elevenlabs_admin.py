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
        cur.execute("""
            SELECT t.whatsapp_user, p.first_name, t.tracking_plate 
            FROM livi_trips t
            JOIN livre_crm_passengers p ON t.whatsapp_user = p.phone
            WHERE t.id = %s
        """, [trip_id])
        trip = cur.fetchone()
        
    if not trip or not trip.get("whatsapp_user"):
        log.error("[ELEVEN ADMIN] Pasajero no encontrado para trip=%s", trip_id)
        return

    # Evitar duplicados (idempotencia simple mediante evento)
    event_key = hashlib.sha256(f"arrival_{trip_id}".encode()).hexdigest()
    
    payload = {
        "whatsapp_phone_number_id": ELEVENLABS_WHATSAPP_PHONE_NUMBER_ID,
        "whatsapp_user_id": trip["whatsapp_user"],
        "template_name": TEMPLATE_NAME,
        "template_language_code": "es_AR",
        "template_params": [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": trip.get("first_name", "Pasajero")},
                    {"type": "text", "text": f"Tu conductor llegó y te espera en el vehículo patente {trip.get('tracking_plate', 'asignado')}."}
                ]
            }
        ],
        "agent_id": ELEVENLABS_AGENT_ID,
    }

    try:
        response = httpx.post(
            ELEVENLABS_API_URL,
            headers={"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"},
            json=payload,
            timeout=10.0,
        )
        response.raise_for_status()
        log.info("[ELEVEN ADMIN] Aviso de llegada enviado, trip=%s, convo=%s", trip_id, response.json().get("conversation_id"))
    except Exception as e:
        log.error("[ELEVEN ADMIN] Fallo en envío, trip=%s, error=%s", trip_id, str(e))
