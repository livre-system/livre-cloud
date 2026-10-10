"""API base para las apps Livre de pasajeros y conductores.

Este módulo es la frontera propia de Livre Cloud. No expone MAGIIS directamente.
"""

import os
import secrets
import uuid
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response

from app.core.database import get_cursor_dep
from app.core.jwt import require_auth
from app.finance import finalize_trip_financials
from app.mobility.schemas import (
    DriverTrip,
    DriverHistoryItem,
    DriverHistoryResponse,
    DriverShiftClosure,
    DriverShiftClosureCreate,
    DriverOperationalStatusResponse,
    DriverOperationalStatusUpdate,
    HandieMessageCreate,
    HandieOpenRequest,
    LocationUpdate,
    PublicTrip,
    StatusUpdate,
    TripMessageCreate,
    TripRequest,
    TripRequestResponse,
)
from app.voice.elevenlabs_trip import send_tracking_message

router = APIRouter(prefix="/mobility", tags=["mobility"])


def _active_device_session(cur, user: dict, device_id: str, *, require_fresh: bool = True):
    try:
        normalized = str(uuid.UUID(device_id.strip()))
    except (AttributeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="device_id inválido") from exc
    freshness = "AND device_last_seen_at >= NOW() - INTERVAL '2 minutes'" if require_fresh else ""
    cur.execute(
        f"""SELECT user_id
           FROM livre_driver_accounts
           WHERE user_id = %s
             AND active = TRUE
             AND active_device_id = %s
             AND device_session_active = TRUE
             {freshness}""",
        (user["id"], normalized),
    )
    if not cur.fetchone():
        raise HTTPException(status_code=401, detail="La sesión de dispositivo no está activa")
    return normalized


def _trip_or_404(cur, trip_id: int):
    cur.execute(
        """
        SELECT id, origen, destino, status, tracking_driver_name, tracking_plate,
               tracking_vehicle_model, tracking_vehicle_color, tracking_latitude,
               tracking_longitude, tracking_location_updated_at
        FROM livi_trips
        WHERE id = %s
        """,
        (trip_id,),
    )
    trip = cur.fetchone()
    if not trip:
        raise HTTPException(status_code=404, detail="Viaje no encontrado")
    return trip


def _public_trip(trip) -> PublicTrip:
    return PublicTrip(
        id=trip["id"],
        origin=trip["origen"],
        destination=trip["destino"],
        status=trip["status"],
        driver_name=trip["tracking_driver_name"],
        plate=trip["tracking_plate"],
        vehicle_model=trip["tracking_vehicle_model"],
        vehicle_color=trip["tracking_vehicle_color"],
        latitude=trip["tracking_latitude"],
        longitude=trip["tracking_longitude"],
        location_updated_at=trip["tracking_location_updated_at"],
    )


@router.post("/requests", response_model=TripRequestResponse, status_code=201)
def request_trip(payload: TripRequest, cur=Depends(get_cursor_dep)):
    """Creates and internally assigns a passenger request to Livre's driver pool."""
    origin = payload.origin.strip()
    destination = payload.destination.strip()
    if origin.casefold() == destination.casefold():
        raise HTTPException(status_code=422, detail="El origen y el destino deben ser distintos")
    local_terms = ("bahía blanca", "bahia blanca", "ingeniero white", "white", "cerri", "general daniel cerro")
    if not any(term in origin.casefold() for term in local_terms) or not any(term in destination.casefold() for term in local_terms):
        raise HTTPException(status_code=422, detail="Indicá una dirección de Bahía Blanca o alrededores")

    tracking_token = secrets.token_urlsafe(32)
    conversation_id = f"web_{uuid.uuid4()}"
    cur.execute(
        """
        INSERT INTO livi_trips
            (conversation_id, origen, destino, whatsapp_user, tracking_token,
             assigned_driver_user_id, tracking_driver_name, status, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, NULL, NULL, 'assigned', NOW(), NOW())
        RETURNING id, tracking_token, status
        """,
        (conversation_id, origin, destination, payload.whatsapp_user.strip(), tracking_token),
    )
    trip = cur.fetchone()
    passenger_url = os.getenv("PASSENGER_APP_URL", "https://livre-pasajeros.vercel.app")
    tracking_url = f"{passenger_url.rstrip('/')}?token={quote(tracking_token)}"
    return TripRequestResponse(
        trip_id=trip["id"],
        tracking_token=trip["tracking_token"],
        tracking_url=tracking_url,
        status=trip["status"],
        message="Pedido recibido y enviado a los conductores Livre disponibles.",
    )


@router.get("/trips/{trip_id}", response_model=PublicTrip)
def get_trip(trip_id: int, cur=Depends(get_cursor_dep), user: dict = Depends(require_auth)):
    """Consulta operativa autenticada para el front de Livre."""
    return _public_trip(_trip_or_404(cur, trip_id))


@router.post("/trips/{trip_id}/status", response_model=PublicTrip)
def update_trip_status(
    trip_id: int,
    payload: StatusUpdate,
    cur=Depends(get_cursor_dep),
    user: dict = Depends(require_auth),
):
    """Actualiza el estado desde el circuito autenticado de conductor/operación."""
    _trip_or_404(cur, trip_id)
    cur.execute(
        """
        UPDATE livi_trips
        SET status = %s, updated_at = NOW()
        WHERE id = %s
        RETURNING id, origen, destino, status, tracking_driver_name, tracking_plate,
                  tracking_vehicle_model, tracking_vehicle_color, tracking_latitude,
                  tracking_longitude, tracking_location_updated_at
        """,
        (payload.status, trip_id),
    )
    row = cur.fetchone()
    if payload.status == "completed":
        finalize_trip_financials(cur, trip_id)
    return _public_trip(row)


@router.post("/trips/{trip_id}/location", response_model=PublicTrip)
def update_trip_location(
    trip_id: int,
    payload: LocationUpdate,
    cur=Depends(get_cursor_dep),
    user: dict = Depends(require_auth),
):
    """Guarda la última ubicación del conductor sin llamar al proveedor externo."""
    _trip_or_404(cur, trip_id)
    cur.execute(
        """
        UPDATE livi_trips
        SET tracking_latitude = %s,
            tracking_longitude = %s,
            tracking_location_updated_at = NOW(),
            tracking_location_visible_at = NOW() + INTERVAL '15 seconds',
            updated_at = NOW()
        WHERE id = %s
        RETURNING id, origen, destino, status, tracking_driver_name, tracking_plate,
                  tracking_vehicle_model, tracking_vehicle_color, tracking_latitude,
                  tracking_longitude, tracking_location_updated_at
        """,
        (payload.latitude, payload.longitude, trip_id),
    )
    return _public_trip(cur.fetchone())


@router.get("/tracking/{tracking_token}", response_model=PublicTrip)
def public_tracking(tracking_token: str, cur=Depends(get_cursor_dep)):
    """Vista pública mínima para el pasajero; solo acepta el token privado del viaje."""
    cur.execute(
        """
        SELECT id, origen, destino, status, tracking_driver_name, tracking_plate,
               tracking_vehicle_model, tracking_vehicle_color, tracking_latitude,
               tracking_longitude, tracking_location_updated_at
        FROM livi_trips
        WHERE tracking_token = %s
        """,
        (tracking_token,),
    )
    trip = cur.fetchone()
    if not trip:
        raise HTTPException(status_code=404, detail="Seguimiento no encontrado")
    return _public_trip(trip)


@router.get("/tracking/{tracking_token}/messages")
def public_trip_messages(tracking_token: str, cur=Depends(get_cursor_dep)):
    """Mensajería del pasajero, aislada del canal interno de base."""
    cur.execute("SELECT id FROM livi_trips WHERE tracking_token = %s", (tracking_token,))
    trip = cur.fetchone()
    if not trip:
        raise HTTPException(status_code=404, detail="Seguimiento no encontrado")
    cur.execute(
        """SELECT id, trip_id, channel, sender_role, sender_id, body, created_at
           FROM livre_trip_messages
           WHERE trip_id = %s AND channel = 'passenger_chat'
           ORDER BY created_at ASC LIMIT 100""",
        (trip["id"],),
    )
    return cur.fetchall()


@router.post("/tracking/{tracking_token}/messages", status_code=201)
def public_send_trip_message(
    tracking_token: str,
    payload: TripMessageCreate,
    cur=Depends(get_cursor_dep),
):
    cur.execute("SELECT id FROM livi_trips WHERE tracking_token = %s", (tracking_token,))
    trip = cur.fetchone()
    if not trip:
        raise HTTPException(status_code=404, detail="Seguimiento no encontrado")
    cur.execute(
        """INSERT INTO livre_trip_messages
           (trip_id, channel, sender_role, sender_id, body)
           VALUES (%s, 'passenger_chat', 'passenger', NULL, %s)
           RETURNING id, trip_id, channel, sender_role, sender_id, body, created_at""",
        (trip["id"], payload.body.strip()),
    )
    return cur.fetchone()


def _driver_trip_or_404(cur, trip_id: int, user: dict):
    """Returns a trip only when it is assigned to the authenticated driver."""
    cur.execute(
        """
        SELECT t.id, t.origen, t.destino, t.status,
               t.tracking_driver_name, t.tracking_plate,
               t.tracking_vehicle_model, t.tracking_vehicle_color,
               t.tracking_latitude, t.tracking_longitude,
               t.tracking_location_updated_at,
               COALESCE(t.service_type_id, selected_quote.transport_type_id::text)::integer AS service_type_id,
               COALESCE(t.service_type_name, CASE selected_quote.transport_type_id
                   WHEN 1 THEN 'Premium'
                   WHEN 2 THEN 'Ahorro'
                   ELSE NULL
               END) AS service_type_name,
               COALESCE(t.fare, selected_quote.price_point_to_point) AS fare,
               t.gross_fare, t.livre_commission, t.driver_net, t.card_payment_confirmed
        FROM livi_trips t
        JOIN livre_driver_accounts a ON a.user_id = t.assigned_driver_user_id
        LEFT JOIN LATERAL (
            SELECT q.transport_type_id, q.price_point_to_point
            FROM magiis_quote_requests qr
            JOIN magiis_quotes q ON q.quote_request_id = qr.id
            WHERE qr.livi_trip_id = t.id
              AND q.transport_type_id = NULLIF(qr.request_payload->>'serviceTypeId', '')::integer
            ORDER BY qr.created_at DESC, q.created_at DESC, q.id DESC
            LIMIT 1
        ) selected_quote ON TRUE
        WHERE t.id = %s AND a.user_id = %s AND a.active = TRUE
        """,
        (trip_id, user["id"]),
    )
    trip = cur.fetchone()
    if not trip:
        raise HTTPException(status_code=404, detail="Viaje no asignado a este conductor")
    return trip


def _driver_trip(trip) -> DriverTrip:
    return DriverTrip(
        id=trip["id"],
        origin=trip["origen"],
        destination=trip["destino"],
        status=trip["status"],
        driver_name=trip["tracking_driver_name"],
        plate=trip["tracking_plate"],
        vehicle_model=trip["tracking_vehicle_model"],
        vehicle_color=trip["tracking_vehicle_color"],
        latitude=trip["tracking_latitude"],
        longitude=trip["tracking_longitude"],
        location_updated_at=trip["tracking_location_updated_at"],
        service_type_id=trip["service_type_id"],
        service_type_name=trip["service_type_name"],
        fare=float(trip["fare"]) if trip["fare"] is not None else None,
        gross_fare=float(trip["gross_fare"]) if trip["gross_fare"] is not None else None,
        livre_commission=float(trip["livre_commission"]) if trip["livre_commission"] is not None else None,
        driver_net=float(trip["driver_net"]) if trip["driver_net"] is not None else None,
        card_payment_confirmed=bool(trip["card_payment_confirmed"]),
    )


def _history_item(trip) -> DriverHistoryItem:
    return DriverHistoryItem(
        **_driver_trip(trip).model_dump(),
        completed_at=trip["completed_at"],
    )


def _closure_result(row, pdf_url: str | None = None) -> DriverShiftClosure:
    return DriverShiftClosure(
        id=str(row["id"]),
        driver_user_id=str(row["driver_user_id"]),
        driver_name=row["driver_name"],
        closure_date=row["closure_date"],
        closed_at=row["closed_at"],
        trip_count=int(row["trip_count"]),
        total_revenue=float(row["total_revenue"] or 0),
        total_commission=float(row["total_commission"] or 0),
        total_driver_net=float(row["total_driver_net"] or 0),
        vehicle_id=row["vehicle_id"],
        vehicle_label=row["vehicle_label"],
        vehicle_model=row["vehicle_model"],
        plate=row["plate"],
        owner_name=row["owner_name"],
        pdf_url=pdf_url,
    )


def _pdf_escape(value: object) -> str:
    return str(value or "").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _summary_pdf(lines: list[str]) -> bytes:
    """Genera el resumen interno sin proveedor externo ni datos de comisión."""
    stream_lines = ["BT", "/F1 11 Tf", "50 780 Td"]
    for index, line in enumerate(lines):
        if index:
            stream_lines.append("0 -18 Td")
        stream_lines.append(f"({_pdf_escape(line)}) Tj")
    stream_lines.append("ET")
    stream = "\n".join(stream_lines).encode("latin-1", "replace")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    pdf.extend("".join(f"{offset:010d} 00000 n \n" for offset in offsets[1:]).encode())
    pdf.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    return bytes(pdf)


@router.get("/driver/operational-status", response_model=DriverOperationalStatusResponse)
def get_driver_operational_status(
    cur=Depends(get_cursor_dep), user: dict = Depends(require_auth)
):
    cur.execute(
        """SELECT operational_status
           FROM livre_driver_accounts
           WHERE user_id = %s AND active = TRUE""",
        (user["id"],),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="Cuenta de conductor no habilitada")
    return {"status": row["operational_status"]}


@router.put("/driver/operational-status", response_model=DriverOperationalStatusResponse)
def update_driver_operational_status(
    payload: DriverOperationalStatusUpdate,
    cur=Depends(get_cursor_dep), user: dict = Depends(require_auth)
):
    cur.execute(
        """UPDATE livre_driver_accounts
           SET operational_status = %s, updated_at = NOW()
           WHERE user_id = %s AND active = TRUE
           RETURNING operational_status""",
        (payload.status, user["id"]),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="Cuenta de conductor no habilitada")
    return {"status": row["operational_status"]}


@router.get("/driver/trips", response_model=list[DriverTrip])
def driver_trips(cur=Depends(get_cursor_dep), user: dict = Depends(require_auth)):
    """Lists every open trip for any conductor in service or with fresh GPS."""
    cur.execute(
        """
        SELECT t.id, t.origen, t.destino, t.status,
               t.tracking_driver_name, t.tracking_plate,
               t.tracking_vehicle_model, t.tracking_vehicle_color,
               t.tracking_latitude, t.tracking_longitude,
               t.tracking_location_updated_at,
               COALESCE(t.service_type_id, selected_quote.transport_type_id::text)::integer AS service_type_id,
               COALESCE(t.service_type_name, CASE selected_quote.transport_type_id
                   WHEN 1 THEN 'Premium'
                   WHEN 2 THEN 'Ahorro'
                   ELSE NULL
               END) AS service_type_name,
               COALESCE(t.fare, selected_quote.price_point_to_point) AS fare,
               t.gross_fare, t.livre_commission, t.driver_net, t.card_payment_confirmed
        FROM livi_trips t
        LEFT JOIN LATERAL (
            SELECT q.transport_type_id, q.price_point_to_point
            FROM magiis_quote_requests qr
            JOIN magiis_quotes q ON q.quote_request_id = qr.id
            WHERE qr.livi_trip_id = t.id
              AND q.transport_type_id = NULLIF(qr.request_payload->>'serviceTypeId', '')::integer
            ORDER BY qr.created_at DESC, q.created_at DESC, q.id DESC
            LIMIT 1
        ) selected_quote ON TRUE
        WHERE (
            (
                EXISTS (
                    SELECT 1 FROM livre_driver_accounts active_driver
                    WHERE active_driver.user_id = %s
                      AND active_driver.operational_status <> 'out_of_service'
                      AND (active_driver.active = TRUE
                           OR active_driver.location_updated_at >= NOW() - INTERVAL '2 minutes')
                )
                AND t.status = 'assigned'
                AND t.assigned_driver_user_id IS NULL
            )
            OR t.assigned_driver_user_id = %s
        )
        ORDER BY t.created_at DESC
        """,
        (user["id"], user["id"]),
    )
    return [_driver_trip(row) for row in cur.fetchall()]


@router.get("/driver/history", response_model=DriverHistoryResponse)
def driver_history(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    cur=Depends(get_cursor_dep),
    user: dict = Depends(require_auth),
):
    """Historial y recaudación desde livi_trips, aislado por conductor."""
    end = date_to or date.today()
    start = date_from or end
    if start > end:
        raise HTTPException(status_code=422, detail="date_from no puede ser posterior a date_to")
    cur.execute(
        """
        SELECT t.id, t.origen, t.destino, t.status,
               t.tracking_driver_name, t.tracking_plate,
               t.tracking_vehicle_model, t.tracking_vehicle_color,
               t.tracking_latitude, t.tracking_longitude,
               t.tracking_location_updated_at, t.completed_at,
               t.service_type_id, t.service_type_name, t.fare,
               t.gross_fare, t.livre_commission, t.driver_net, t.card_payment_confirmed
        FROM livi_trips t
        WHERE t.assigned_driver_user_id = %s
          AND t.status IN ('completed', 'cancelled')
          AND COALESCE(t.completed_at, t.updated_at) >= %s::date
          AND COALESCE(t.completed_at, t.updated_at) < (%s::date + INTERVAL '1 day')
        ORDER BY COALESCE(t.completed_at, t.updated_at) DESC, t.id DESC
        """,
        (user["id"], start, end),
    )
    rows = cur.fetchall()
    items = [_history_item(row) for row in rows]
    return DriverHistoryResponse(
        items=items,
        total=len(items),
        total_revenue=float(sum((row["gross_fare"] or Decimal("0")) for row in rows if row["status"] == "completed")),
        total_commission=float(sum((row["livre_commission"] or Decimal("0")) for row in rows if row["status"] == "completed")),
        total_driver_net=float(sum((row["driver_net"] or Decimal("0")) for row in rows if row["status"] == "completed")),
        date_from=start,
        date_to=end,
    )


def _closure_query(cur, closure_id, user_id=None):
    predicate = "c.id = %s"
    params = [closure_id]
    if user_id is not None:
        predicate += " AND c.driver_user_id = %s"
        params.append(user_id)
    cur.execute(
        f"""
        SELECT c.id, c.driver_user_id, u.name AS driver_name, c.closure_date,
               c.closed_at, c.trip_count, c.total_revenue,
               c.total_commission, c.total_driver_net,
               c.vehicle_id, c.vehicle_label, c.vehicle_model, c.plate, c.owner_name
        FROM livre_driver_shift_closures c
        JOIN livre_users u ON u.id = c.driver_user_id
        WHERE {predicate}
        """,
        params,
    )
    return cur.fetchone()


@router.get("/driver/shift-closures", response_model=list[DriverShiftClosure])
def driver_shift_closures(cur=Depends(get_cursor_dep), user: dict = Depends(require_auth)):
    cur.execute(
        """
        SELECT c.id, c.driver_user_id, u.name AS driver_name, c.closure_date,
               c.closed_at, c.trip_count, c.total_revenue,
               c.total_commission, c.total_driver_net,
               c.vehicle_id, c.vehicle_label, c.vehicle_model, c.plate, c.owner_name
        FROM livre_driver_shift_closures c
        JOIN livre_users u ON u.id = c.driver_user_id
        WHERE c.driver_user_id = %s
        ORDER BY c.closure_date DESC, c.closed_at DESC
        """,
        (user["id"],),
    )
    return [_closure_result(row) for row in cur.fetchall()]


@router.post("/driver/shift-closures", response_model=DriverShiftClosure, status_code=201)
def close_driver_shift(
    payload: DriverShiftClosureCreate,
    cur=Depends(get_cursor_dep),
    user: dict = Depends(require_auth),
):
    """Persiste un único cierre diario por conductor y guarda sus viajes."""
    lock_key = f"driver-shift:{user['id']}:{payload.closure_date.isoformat()}"
    cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (lock_key,))
    cur.execute(
        "SELECT id FROM livre_driver_shift_closures WHERE driver_user_id = %s AND closure_date = %s",
        (user["id"], payload.closure_date),
    )
    if cur.fetchone():
        raise HTTPException(status_code=409, detail="shift already closed")
    cur.execute(
        """
        SELECT count(*) AS trip_count,
               COALESCE(sum(CASE WHEN status = 'completed' THEN gross_fare ELSE 0 END), 0) AS total_revenue,
               COALESCE(sum(CASE WHEN status = 'completed' THEN livre_commission ELSE 0 END), 0) AS total_commission,
               COALESCE(sum(CASE WHEN status = 'completed' THEN driver_net ELSE 0 END), 0) AS total_driver_net
        FROM livi_trips
        WHERE assigned_driver_user_id = %s AND status IN ('completed', 'cancelled')
          AND COALESCE(completed_at, updated_at) >= %s::date
          AND COALESCE(completed_at, updated_at) < (%s::date + INTERVAL '1 day')
        """,
        (user["id"], payload.closure_date, payload.closure_date),
    )
    totals = cur.fetchone()
    cur.execute(
        """
        SELECT v.vehicle_id, v.vehicle_label, v.vehicle_model, v.plate, v.owner_name,
               COALESCE(v.plate, t.tracking_plate) AS effective_plate
        FROM livre_driver_vehicles v
        LEFT JOIN LATERAL (
            SELECT tracking_plate FROM livi_trips
            WHERE assigned_driver_user_id = %s AND status IN ('completed', 'cancelled')
              AND COALESCE(completed_at, updated_at) >= %s::date
              AND COALESCE(completed_at, updated_at) < (%s::date + INTERVAL '1 day')
            ORDER BY COALESCE(completed_at, updated_at) DESC LIMIT 1
        ) t ON TRUE
        WHERE v.driver_user_id = %s AND v.is_primary = TRUE
        ORDER BY v.updated_at DESC LIMIT 1
        """,
        (user["id"], payload.closure_date, payload.closure_date, user["id"]),
    )
    vehicle = cur.fetchone() or {}
    cur.execute(
        """
        INSERT INTO livre_driver_shift_closures
            (driver_user_id, closure_date, trip_count, total_revenue,
             total_commission, total_driver_net,
             vehicle_id, vehicle_label, vehicle_model, plate, owner_name)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, COALESCE(%s, %s), %s)
        RETURNING id
        """,
        (user["id"], payload.closure_date, totals["trip_count"], totals["total_revenue"],
         totals["total_commission"], totals["total_driver_net"],
         vehicle.get("vehicle_id"), vehicle.get("vehicle_label"), vehicle.get("vehicle_model"),
         vehicle.get("plate"), vehicle.get("effective_plate"), vehicle.get("owner_name")),
    )
    closure_id = cur.fetchone()["id"]
    cur.execute(
        """
        INSERT INTO livre_driver_shift_closure_trips (closure_id, trip_id, fare)
        SELECT %s, id, gross_fare
        FROM livi_trips
        WHERE assigned_driver_user_id = %s AND status IN ('completed', 'cancelled')
          AND COALESCE(completed_at, updated_at) >= %s::date
          AND COALESCE(completed_at, updated_at) < (%s::date + INTERVAL '1 day')
        """,
        (closure_id, user["id"], payload.closure_date, payload.closure_date),
    )
    row = _closure_query(cur, closure_id, user["id"])
    return _closure_result(row, f"/mobility/driver/shift-closures/{closure_id}/pdf")


@router.get("/driver/shift-closures/{closure_id}/pdf")
def driver_shift_closure_pdf(
    closure_id: str,
    cur=Depends(get_cursor_dep),
    user: dict = Depends(require_auth),
):
    row = _closure_query(cur, closure_id, user["id"])
    if not row:
        raise HTTPException(status_code=404, detail="Cierre no encontrado")
    cur.execute(
        """SELECT t.id, t.origen, t.destino, ct.fare,
                  t.livre_commission, t.driver_net
           FROM livre_driver_shift_closure_trips ct
           JOIN livi_trips t ON t.id = ct.trip_id
           WHERE ct.closure_id = %s ORDER BY t.id""",
        (closure_id,),
    )
    trip_rows = cur.fetchall()
    cur.execute("UPDATE livre_driver_shift_closures SET pdf_generated_at = NOW() WHERE id = %s", (closure_id,))
    lines = [
        "Livre - Resumen diario del conductor",
        f"Conductor: {row['driver_name']}",
        f"Vehiculo: {row['vehicle_model'] or row['vehicle_label'] or 'No informado'}",
        f"Patente: {row['plate'] or 'No informada'}",
        f"Propietario: {row['owner_name'] or 'No informado'}",
        f"Fecha: {row['closure_date'].isoformat()}",
        f"Cantidad de viajes: {row['trip_count']}",
        f"Recaudacion bruta: ARS {row['total_revenue'] or 0}",
        f"Comision Livre (15%): ARS {row['total_commission'] or 0}",
        f"Neto conductor (85%): ARS {row['total_driver_net'] or 0}",
        "Pago con tarjeta confirmado: No",
        "Detalle de viajes:",
    ]
    lines.extend(f"#{trip['id']} {trip['origen']} -> {trip['destino']} | ARS {trip['fare'] or 0}" for trip in trip_rows)
    return Response(
        content=_summary_pdf(lines),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="livre-cierre-{row["closure_date"]}.pdf"'},
    )


@router.get("/driver/trips/{trip_id}/messages")
def driver_trip_messages(
    trip_id: int,
    cur=Depends(get_cursor_dep),
    user: dict = Depends(require_auth),
):
    _driver_trip_or_404(cur, trip_id, user)
    cur.execute(
        """SELECT id, trip_id, channel, sender_role, sender_id, body, created_at
           FROM livre_trip_messages
           WHERE trip_id = %s AND channel = 'passenger_chat'
           ORDER BY created_at ASC LIMIT 100""",
        (trip_id,),
    )
    return cur.fetchall()


@router.post("/driver/trips/{trip_id}/messages", status_code=201)
def driver_send_trip_message(
    trip_id: int,
    payload: TripMessageCreate,
    cur=Depends(get_cursor_dep),
    user: dict = Depends(require_auth),
):
    _driver_trip_or_404(cur, trip_id, user)
    cur.execute(
        """INSERT INTO livre_trip_messages
           (trip_id, channel, sender_role, sender_id, body)
           VALUES (%s, 'passenger_chat', 'driver', %s, %s)
           RETURNING id, trip_id, channel, sender_role, sender_id, body, created_at""",
        (trip_id, user["id"], payload.body.strip()),
    )
    return cur.fetchone()


@router.post("/driver/handie", status_code=201)
def open_handie(
    payload: HandieOpenRequest,
    cur=Depends(get_cursor_dep),
    user: dict = Depends(require_auth),
):
    if payload.trip_id is not None:
        _driver_trip_or_404(cur, payload.trip_id, user)
    cur.execute(
        """INSERT INTO livre_handie_sessions (driver_user_id, trip_id)
           VALUES (%s, %s)
           RETURNING id, trip_id, status, created_at""",
        (user["id"], payload.trip_id),
    )
    return cur.fetchone()


@router.post("/driver/handie/{session_id}/messages", status_code=201)
def send_handie_message(
    session_id: str,
    payload: HandieMessageCreate,
    cur=Depends(get_cursor_dep),
    user: dict = Depends(require_auth),
):
    cur.execute(
        """SELECT id, trip_id FROM livre_handie_sessions
           WHERE id = %s AND driver_user_id = %s AND status = 'open'""",
        (session_id, user["id"]),
    )
    session = cur.fetchone()
    if not session:
        raise HTTPException(status_code=404, detail="Handie cerrado o inexistente")
    if not payload.body.strip():
        raise HTTPException(status_code=422, detail="El mensaje no puede estar vacío")
    if session["trip_id"]:
        cur.execute(
            """INSERT INTO livre_trip_messages
               (trip_id, channel, sender_role, sender_id, body)
               VALUES (%s, 'base', 'driver', %s, %s)""",
            (session["trip_id"], user["id"], payload.body.strip()),
        )
    return {"sent": True, "session_id": session_id}


@router.post("/driver/handie/{session_id}/close")
def close_handie(
    session_id: str,
    cur=Depends(get_cursor_dep),
    user: dict = Depends(require_auth),
):
    cur.execute(
        """UPDATE livre_handie_sessions SET status = 'closed', closed_at = now()
           WHERE id = %s AND driver_user_id = %s AND status = 'open'
           RETURNING id, status, closed_at""",
        (session_id, user["id"]),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Handie cerrado o inexistente")
    return row


@router.get("/driver/handie/{session_id}/messages")
def driver_handie_messages(
    session_id: str,
    cur=Depends(get_cursor_dep),
    user: dict = Depends(require_auth),
):
    cur.execute(
        """SELECT s.id FROM livre_handie_sessions s
           WHERE s.id = %s AND s.driver_user_id = %s""",
        (session_id, user["id"]),
    )
    if not cur.fetchone():
        raise HTTPException(status_code=404, detail="Handie inexistente")
    cur.execute(
        """SELECT m.id, m.trip_id, m.sender_role, m.sender_id, m.body, m.created_at
           FROM livre_trip_messages m
           JOIN livre_handie_sessions s ON s.trip_id = m.trip_id
           WHERE s.id = %s AND m.channel = 'base'
           ORDER BY m.created_at ASC LIMIT 100""",
        (session_id,),
    )
    return cur.fetchall()


@router.get("/base/messages")
def base_messages(cur=Depends(get_cursor_dep), user: dict = Depends(require_auth)):
    """Bandeja del CRM para el canal base; no mezcla el chat del pasajero."""
    cur.execute(
        """SELECT m.id, m.trip_id, m.sender_role, m.sender_id, m.body, m.created_at,
                  t.origen, t.destino
           FROM livre_trip_messages m
           JOIN livi_trips t ON t.id = m.trip_id
           WHERE m.channel = 'base'
           ORDER BY m.created_at DESC LIMIT 100"""
    )
    return cur.fetchall()


@router.post("/base/messages", status_code=201)
def base_send_message(
    payload: TripMessageCreate,
    cur=Depends(get_cursor_dep),
    user: dict = Depends(require_auth),
):
    """Envio desde CRM al handie abierto del conductor."""
    cur.execute(
        """SELECT id, trip_id FROM livre_handie_sessions
           WHERE status = 'open' ORDER BY created_at DESC LIMIT 1"""
    )
    session = cur.fetchone()
    if not session:
        raise HTTPException(status_code=409, detail="No hay un handie abierto")
    if not payload.body.strip():
        raise HTTPException(status_code=422, detail="El mensaje no puede estar vacío")
    cur.execute(
        """INSERT INTO livre_trip_messages
           (trip_id, channel, sender_role, sender_id, body)
           VALUES (%s, 'base', 'base', %s, %s)
           RETURNING id, trip_id, channel, sender_role, sender_id, body, created_at""",
        (session["trip_id"], user["id"], payload.body.strip()),
    )
    return cur.fetchone()


@router.post("/driver/trips/{trip_id}/status", response_model=DriverTrip)
def driver_update_status(
    trip_id: int,
    payload: StatusUpdate,
    cur=Depends(get_cursor_dep),
    user: dict = Depends(require_auth),
):
    """Claims an assigned trip for the first conductor who takes it."""
    try:
        trip = _driver_trip_or_404(cur, trip_id, user)
    except HTTPException:
        if payload.status != "arriving":
            raise
        cur.execute(
            """
            UPDATE livi_trips t
            SET assigned_driver_user_id = %s,
                tracking_driver_name = u.name,
                status = %s,
                updated_at = NOW()
            FROM livre_users u
            JOIN livre_driver_accounts a ON a.user_id = u.id
            WHERE t.id = %s
              AND t.status = 'assigned'
              AND t.assigned_driver_user_id IS NULL
              AND a.user_id = %s
              AND a.operational_status <> 'out_of_service'
              AND (a.active = TRUE
                   OR a.location_updated_at >= NOW() - INTERVAL '2 minutes')
            RETURNING t.id
            """,
            (user["id"], payload.status, trip_id, user["id"]),
        )
        if not cur.fetchone():
            raise HTTPException(status_code=409, detail="El viaje ya fue tomado por otro conductor")
        trip = _driver_trip_or_404(cur, trip_id, user)
    previous_status = trip["status"]
    if previous_status != "assigned" or payload.status != "arriving":
        cur.execute(
            """
            UPDATE livi_trips
            SET status = %s,
                completed_at = CASE WHEN %s = 'completed' THEN NOW() ELSE completed_at END,
                updated_at = NOW()
            WHERE id = %s AND assigned_driver_user_id = %s
            """,
            (payload.status, payload.status, trip_id, user["id"]),
        )
    elif trip["status"] == "assigned":
        cur.execute(
            """
            UPDATE livi_trips SET status = 'arriving', updated_at = NOW()
            WHERE id = %s AND assigned_driver_user_id = %s
            """,
            (trip_id, user["id"]),
        )
    if payload.status == "completed":
        finalize_trip_financials(cur, trip_id)
    if previous_status == "assigned" and payload.status == "arriving":
        cur.execute("SELECT * FROM livi_trips WHERE id = %s", (trip_id,))
        arriving_trip = cur.fetchone()
        try:
            conversation_id = send_tracking_message(arriving_trip)
            cur.execute(
                """UPDATE livi_trips
                   SET tracking_message_sent_at = NOW(),
                       tracking_message_conversation_id = %s,
                       tracking_message_error = NULL,
                       updated_at = NOW()
                   WHERE id = %s""",
                (conversation_id or None, trip_id),
            )
        except Exception as exc:
            cur.execute(
                """UPDATE livi_trips
                   SET tracking_message_error = %s, updated_at = NOW()
                   WHERE id = %s""",
                (str(exc)[:500], trip_id),
            )
    return _driver_trip(_driver_trip_or_404(cur, trip_id, user))


@router.post("/driver/location")
def driver_update_live_location(
    payload: LocationUpdate,
    cur=Depends(get_cursor_dep),
    user: dict = Depends(require_auth),
):
    """Stores the driver's latest GPS position even without an active trip."""
    device_id = _active_device_session(cur, user, payload.device_id)
    cur.execute(
        """
        UPDATE livre_driver_accounts
        SET current_latitude = %s,
            current_longitude = %s,
            location_updated_at = NOW(),
            updated_at = NOW()
        WHERE user_id = %s AND active = TRUE
          AND active_device_id = %s
          AND device_session_active = TRUE
        RETURNING user_id, location_updated_at
        """,
        (payload.latitude, payload.longitude, user["id"], device_id),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="La cuenta de conductor no está activa")
    cur.execute(
        """UPDATE livre_driver_accounts
           SET device_last_seen_at = NOW(), updated_at = NOW()
           WHERE user_id = %s AND active_device_id = %s AND device_session_active = TRUE""",
        (user["id"], device_id),
    )
    return {"status": "updated", "location_updated_at": row["location_updated_at"]}


@router.delete("/driver/location")
def driver_clear_live_location(
    device_id: str | None = Header(default=None, alias="X-Device-ID"),
    cur=Depends(get_cursor_dep),
    user: dict = Depends(require_auth),
):
    """Clears only the authenticated driver's current device location on logout."""
    normalized_device_id = None
    if device_id:
        normalized_device_id = _active_device_session(cur, user, device_id, require_fresh=False)
    cur.execute(
        """
        UPDATE livre_driver_accounts
        SET current_latitude = NULL,
            current_longitude = NULL,
            location_updated_at = NULL,
            device_session_active = CASE WHEN %s IS NULL THEN device_session_active ELSE FALSE END,
            active_device_id = CASE WHEN %s IS NULL THEN active_device_id ELSE NULL END,
            device_last_seen_at = CASE WHEN %s IS NULL THEN device_last_seen_at ELSE NULL END,
            updated_at = NOW()
        WHERE user_id = %s AND active = TRUE
          AND (%s IS NULL OR active_device_id = %s)
        RETURNING user_id
        """,
        (normalized_device_id, normalized_device_id, normalized_device_id,
         user["id"], normalized_device_id, normalized_device_id),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="La cuenta de conductor no está activa")
    return {"status": "cleared"}


@router.post("/driver/trips/{trip_id}/location", response_model=DriverTrip)
def driver_update_location(
    trip_id: int,
    payload: LocationUpdate,
    cur=Depends(get_cursor_dep),
    user: dict = Depends(require_auth),
):
    """Stores GPS only for a trip assigned to the authenticated driver."""
    _active_device_session(cur, user, payload.device_id)
    _driver_trip_or_404(cur, trip_id, user)
    cur.execute(
        """
        UPDATE livi_trips
        SET tracking_latitude = %s,
            tracking_longitude = %s,
            tracking_location_updated_at = NOW(),
            tracking_location_visible_at = NOW() + INTERVAL '15 seconds',
            updated_at = NOW()
        WHERE id = %s
        """,
        (payload.latitude, payload.longitude, trip_id),
    )
    return _driver_trip(_driver_trip_or_404(cur, trip_id, user))
