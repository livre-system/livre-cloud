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
# Forzar plantilla si la variable de entorno no la resuelve correctamente
TEMPLATE_NAME = os.getenv("ELEVENLABS_WHATSAPP_ADMIN_NOTICE_TEMPLATE_NAME", "livre_admin_notice").strip()
if not TEMPLATE_NAME:
    TEMPLATE_NAME = "livre_admin_notice"
log.info("[ELEVEN ADMIN] Configuración de plantilla: %s", TEMPLATE_NAME)

def send_arrival_whatsapp(trip_id: int, cur=None):
    """Envía WhatsApp de aviso de llegada usando plantilla administrativa."""
    if not TEMPLATE_NAME:
        log.warning("[ELEVEN ADMIN] Plantilla no configurada, omitiendo envío para trip=%s", trip_id)
        return

    # Usar el cursor proporcionado o abrir uno nuevo
    def _do_work(cursor):
        cursor.execute("""
            SELECT t.whatsapp_user, p.first_name, t.tracking_plate 
            FROM livi_trips t
            LEFT JOIN livre_crm_passengers p ON t.whatsapp_user = p.phone
            WHERE t.id = %s
        """, [trip_id])
        trip = cursor.fetchone()
        
        if not trip or not trip.get("whatsapp_user"):
            log.error("[ELEVEN ADMIN] Pasajero no encontrado o sin ID para trip=%s", trip_id)
            return None, None

        first_name = params[0] if params else "Pasajero"
        plate = params[1] if params else "tu conductor"
        return trip, (first_name, plate)

    if cur:
        trip, params = _do_work(cur)
    else:
        with get_cursor() as conn_cur:
            trip, params = _do_work(conn_cur)
            
    if not trip: return

    # Idempotencia: Verificar si ya se envió este aviso
    check_cur = cur if cur else get_cursor()
    with check_cur as c:
        c.execute("SELECT id FROM livre_trip_event_outbox WHERE trip_id = %s AND event_type = 'arrival_notice_sent' AND status = 'sent'", [trip_id])
        if c.fetchone():
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

