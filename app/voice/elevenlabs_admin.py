import os
import httpx
import logging
from app.core.database import get_cursor

log = logging.getLogger(__name__)

META_GRAPH_API_VERSION = os.getenv("META_GRAPH_API_VERSION", "v21.0")
META_WHATSAPP_API_URL = "https://graph.facebook.com/{}/messages"
META_WHATSAPP_ACCESS_TOKEN = os.getenv("META_WHATSAPP_ACCESS_TOKEN", "")
ELEVENLABS_AGENT_ID = os.getenv("ELEVENLABS_AGENT_ID", "agent_3701m0dfvnmmemptqcm2jym9wdvv")
ELEVENLABS_WHATSAPP_PHONE_NUMBER_ID = os.getenv("ELEVENLABS_WHATSAPP_PHONE_NUMBER_ID", "1364913613361192")
ELEVENLABS_WHATSAPP_TEMPLATE_LANGUAGE = os.getenv("ELEVENLABS_WHATSAPP_TEMPLATE_LANGUAGE", "es_AR").strip() or "es_AR"
TEMPLATE_NAME = os.getenv("ELEVENLABS_WHATSAPP_ADMIN_NOTICE_TEMPLATE_NAME", "livre_admin_notice").strip() or "livre_admin_notice"
log.info("[ELEVEN ADMIN] Configuración de plantilla: %s", TEMPLATE_NAME)

def send_arrival_message(trip: dict) -> str:
    """Envía el aviso de llegada por ElevenLabs usando la plantilla aprobada."""
    api_key = os.getenv("ELEVENLABS_API_KEY")
    recipient = str(trip.get("whatsapp_user") or "").strip()
    template_name = os.getenv("ELEVENLABS_WHATSAPP_ADMIN_NOTICE_TEMPLATE_NAME", "livre_admin_notice").strip()
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY no configurada")
    if not recipient:
        raise RuntimeError(f"El viaje {trip.get('id')} no tiene whatsapp_user")
    payload = {
        "whatsapp_phone_number_id": ELEVENLABS_WHATSAPP_PHONE_NUMBER_ID,
        "whatsapp_user_id": recipient,
        "template_name": template_name,
        "template_language_code": ELEVENLABS_WHATSAPP_TEMPLATE_LANGUAGE,
        "template_params": [{"type": "body", "parameters": []}],
        "agent_id": ELEVENLABS_AGENT_ID,
    }
    response = httpx.post(
        "https://api.elevenlabs.io/v1/convai/whatsapp/outbound-message",
        headers={"xi-api-key": api_key, "Content-Type": "application/json"},
        json=payload,
        timeout=20.0,
    )
    if response.is_error:
        log.error("[ELEVEN ARRIVAL] trip=%s status=%s body=%s", trip.get("id"), response.status_code, response.text[:500])
        response.raise_for_status()
    result = response.json()
    return result.get("conversation_id") or ""


def _send_arrival_with_cursor(trip_id: int, my_cur):
    """Realiza el envío usando un cursor ya abierto."""
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
        raise RuntimeError(f"Viaje {trip_id} sin whatsapp_user")

    return send_arrival_message(trip)


def send_arrival_whatsapp(trip_id: int, cur=None):
    """Compatibilidad: obtiene el viaje y envía siempre por ElevenLabs."""
    if cur is not None:
        return _send_arrival_with_cursor(trip_id, cur)
    with get_cursor() as managed_cur:
        return _send_arrival_with_cursor(trip_id, managed_cur)

