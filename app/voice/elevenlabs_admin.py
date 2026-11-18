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

def send_arrival_whatsapp(trip_id: int, cur=None):
    """Envía WhatsApp de aviso de llegada usando plantilla administrativa."""
    if not TEMPLATE_NAME:
        log.warning("[ELEVEN ADMIN] Plantilla no configurada, omitiendo envío para trip=%s", trip_id)
        return

    # El worker usa su propia conexión; el router puede pasar un cursor transaccional.
    if cur is not None:
        return _send_arrival_with_cursor(trip_id, cur)
    with get_cursor() as managed_cur:
        return _send_arrival_with_cursor(trip_id, managed_cur)


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

    # Valores directos
    notice_text = "El conductor está afuera"

    # Idempotencia
    my_cur.execute("SELECT id FROM livre_trip_event_outbox WHERE trip_id = %s AND event_type = 'arrival_notice_sent' AND status = 'sent'", [trip_id])
    if my_cur.fetchone():
        return

    if not META_WHATSAPP_ACCESS_TOKEN:
        raise RuntimeError("META_WHATSAPP_ACCESS_TOKEN no configurado")
    payload = {
        "messaging_product": "whatsapp",
        "to": trip["whatsapp_user"],
        "type": "template",
        "template": {
            "name": TEMPLATE_NAME,
            "language": {"code": ELEVENLABS_WHATSAPP_TEMPLATE_LANGUAGE},
        },
    }

    try:
        response = httpx.post(
            META_WHATSAPP_API_URL.format(f"{META_GRAPH_API_VERSION}/{ELEVENLABS_WHATSAPP_PHONE_NUMBER_ID}"),
            json=payload,
            headers={"Authorization": f"Bearer {META_WHATSAPP_ACCESS_TOKEN}", "Content-Type": "application/json"},
            timeout=10.0
        )
        if response.is_error:
            log.info("[ELEVEN ADMIN TRACE] stage=provider_rejected trip_id=%s status=%s", trip_id, response.status_code)
            response.raise_for_status()
        result = response.json()
        conversation_id = result.get("conversation_id") or ""
        log.info("[ELEVEN ADMIN TRACE] stage=provider_accepted trip_id=%s conversation_id_present=%s", trip_id, bool(conversation_id))
        log.info("[ELEVEN ADMIN] Aviso de llegada aceptado, trip=%s", trip_id)
    except Exception as e:
        log.error("[ELEVEN ADMIN] Fallo en envío, trip=%s, error=%s", trip_id, str(e))
        raise

