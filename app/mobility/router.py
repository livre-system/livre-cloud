"""API base para las apps Livre de pasajeros y conductores.

Este módulo es la frontera propia de Livre Cloud. No expone MAGIIS directamente.
"""

import logging
import os
import secrets
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response

from app.core.database import get_cursor_dep
from app.core.jwt import optional_auth, require_auth, require_driver
from app.core.rate_limit import client_key, gps_limiter, trip_action_limiter
from app.core.operational_events import record_trip_event
from app.integrations.google_maps import GeocodingError, geocode_address, route_distance_km, route_geometry
from app.integrations.historical_quote import FARE_SOURCE_COMMERCIAL, commercial_fare, historical_options
from app.mobility.schemas import (
    DriverTrip,
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
    TripQuoteRequest,
    TripRequestResponse,
)
from app.voice.elevenlabs_trip import send_tracking_message


PUBLIC_TRACKING_TOKEN_TTL_HOURS = max(1, int(os.getenv("PASSENGER_TRACKING_TOKEN_TTL_HOURS", "24")))


router = APIRouter(prefix="/mobility", tags=["mobility"])
log = logging.getLogger("uvicorn.error")


def _require_current_driver_device(cur, user: dict, device_id: str | None) -> None:
    """Require a valid JWT device bound to its own active driver session."""
    if not device_id or device_id != user.get("device_id"):
        log.warning(
            "driver_location_rejected event=device_mismatch driver_user_id=%s device_id=%s",
            user.get("id"),
            device_id or "missing",
        )
        raise HTTPException(status_code=401, detail="Sesión de dispositivo inválida")
    cur.execute(
        """SELECT s.user_id, s.device_id
           FROM livre_driver_device_sessions s
           JOIN livre_driver_accounts a ON a.user_id = s.user_id
           WHERE s.user_id = %s
             AND s.device_id = %s
             AND s.active = TRUE
             AND a.active = TRUE""",
        (user["id"], device_id),
    )
    if not cur.fetchone():
        log.warning(
            "driver_location_rejected event=session_invalid driver_user_id=%s device_id=%s",
            user.get("id"),
            device_id,
        )
        raise HTTPException(status_code=401, detail="Sesión de dispositivo revocada")


def _trip_or_404(cur, trip_id: int):
    cur.execute(
        """
        SELECT id, origen, destino, status, tracking_driver_name, tracking_plate,
               tracking_vehicle_model, tracking_vehicle_color, tracking_latitude,
                              tracking_longitude, tracking_location_updated_at, fare, original_fare,
                              discount_percentage, discount_amount, final_fare, fare_source
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
        fare=float(trip["fare"]) if trip.get("fare") is not None else None,
        original_fare=float(trip["original_fare"]) if trip.get("original_fare") is not None else None,
        discount_percentage=float(trip["discount_percentage"]) if trip.get("discount_percentage") is not None else None,
        discount_amount=float(trip["discount_amount"]) if trip.get("discount_amount") is not None else None,
        final_fare=float(trip["final_fare"]) if trip.get("final_fare") is not None else None,
        fare_source=trip.get("fare_source"),
    )


@router.post("/quote")
def quote_trip(payload: TripQuoteRequest, request: Request):
    """Calcula el importe con la tarifa vigente sin crear un viaje."""
    trip_action_limiter.check(client_key(request, "trip-quote"))
    origin_text = payload.origin.strip()
    destination_text = payload.destination.strip()
    local_terms = ("bahía blanca", "bahia blanca", "ingeniero white", "white", "cerri", "general daniel cerro")
    if not any(term in origin_text.casefold() for term in local_terms) or not any(term in destination_text.casefold() for term in local_terms):
        raise HTTPException(status_code=422, detail="Indicá una dirección de Bahía Blanca o alrededores")
    try:
        origin = geocode_address(origin_text)
        destination = geocode_address(destination_text)
        distance_km = route_distance_km(origin, destination)
        options = historical_options(Decimal(str(distance_km)))
        option = next((item for item in options if str(item.get("transportTypeId")) == "2"), options[0])
    except GeocodingError as exc:
        raise HTTPException(status_code=422, detail="No pudimos ubicar esas direcciones") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="La tarifa no está disponible en este momento") from exc
    return {
        "fare": float(option["pricePointToPoint"]),
        "currency": "ARS",
        "service_type_id": int(option["transportTypeId"]),
        "distance_km": option["routeDistance"],
        "origin": origin.get("formatted_address") or origin_text,
        "destination": destination.get("formatted_address") or destination_text,
    }


@router.post("/requests", response_model=TripRequestResponse, status_code=201)
def request_trip(payload: TripRequest, request: Request, cur=Depends(get_cursor_dep), user: dict | None = Depends(optional_auth)):
    """Creates a passenger request; guests remain supported during migration."""
    trip_action_limiter.check(client_key(request, "trip-request"))
    if user and "driver" in (user.get("roles") or []):
        raise HTTPException(status_code=403, detail="Una cuenta de conductor no puede pedir viajes")
    origin = payload.origin.strip()
    destination = payload.destination.strip()
    if origin.casefold() == destination.casefold():
        raise HTTPException(status_code=422, detail="El origen y el destino deben ser distintos")
    local_terms = ("bahía blanca", "bahia blanca", "ingeniero white", "white", "cerri", "general daniel cerro")
    if not any(term in origin.casefold() for term in local_terms) or not any(term in destination.casefold() for term in local_terms):
        raise HTTPException(status_code=422, detail="Indicá una dirección de Bahía Blanca o alrededores")

    tracking_token = secrets.token_urlsafe(32)
    financials = commercial_fare(payload.fare, service_type_id=str(payload.service_type_id))
    conversation_id = f"web_{uuid.uuid4()}"
    passenger_identity = f"user:{user['id']}" if user else payload.whatsapp_user.strip()
    cur.execute(
        """
        INSERT INTO livi_trips
            (conversation_id, user_id, origen, destino, whatsapp_user, tracking_token,
             assigned_driver_user_id, tracking_driver_name, service_type_id,
             fare, original_fare, discount_percentage, discount_amount, final_fare, fare_source,
             activity_type, source, status, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, NULL, NULL, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'searching', NOW(), NOW())
        RETURNING id, tracking_token, status
        """,
        (
            conversation_id,
            user["id"] if user else None,
            origin,
            destination,
            passenger_identity,
            tracking_token,
            payload.service_type_id,
            financials["original_fare"],
            financials["original_fare"],
            financials["discount_percentage"],
            financials["discount_amount"],
            financials["final_fare"],
            FARE_SOURCE_COMMERCIAL,
            payload.activity_type,
            "web",
        ),
    )
    trip = cur.fetchone()
    passenger_url = os.getenv("PASSENGER_APP_URL", "https://livre-pasajeros-production.up.railway.app")
    tracking_url = f"{passenger_url.rstrip('/')}?token={quote(tracking_token)}"
    return TripRequestResponse(
        trip_id=trip["id"],
        tracking_token=trip["tracking_token"],
        tracking_url=tracking_url,
        status=trip["status"],
        message="Pedido recibido y enviado a los conductores Livre disponibles.",
        fare=float(financials["final_fare"]),
    )


@router.get("/trips/{trip_id}", response_model=PublicTrip)
def get_trip(trip_id: int, cur=Depends(get_cursor_dep), user: dict = Depends(require_auth)):
    """Consulta operativa autenticada para el front de Livre."""
    return _public_trip(_trip_or_404(cur, trip_id))


@router.post("/trips/{trip_id}/status", response_model=PublicTrip)
def update_trip_status(
    trip_id: int,
    payload: StatusUpdate,
    request: Request,
    cur=Depends(get_cursor_dep),
    user: dict = Depends(require_driver),
):
    """Actualiza el estado desde el circuito autenticado de conductor/operación."""
    trip_action_limiter.check(client_key(request, "trip-status"))
    trip = _driver_trip_or_404(cur, trip_id, user)
    cur.execute(
        """
        UPDATE livi_trips
        SET status = %s, updated_at = NOW()
        WHERE id = %s AND assigned_driver_user_id = %s
        RETURNING id, origen, destino, status, tracking_driver_name, tracking_plate,
                  tracking_vehicle_model, tracking_vehicle_color, tracking_latitude,
                                 tracking_longitude, tracking_location_updated_at, fare, original_fare,
                                 discount_percentage, discount_amount, final_fare, fare_source
        """,
        (payload.status, trip_id, user["id"]),
    )
    updated = cur.fetchone()
    record_trip_event(
        cur, trip_id=trip_id, actor_user_id=user["id"], actor_role="driver",
        event_type="status_changed", previous_status=trip["status"],
        new_status=payload.status,
    )
    return _public_trip(updated)


@router.post("/trips/{trip_id}/location", response_model=PublicTrip)
def update_trip_location(
    trip_id: int,
    payload: LocationUpdate,
    request: Request,
    cur=Depends(get_cursor_dep),
    user: dict = Depends(require_driver),
):
    """Guarda la última ubicación del conductor sin llamar al proveedor externo."""
    gps_limiter.check(client_key(request, f"gps:{user.get('id', 'unknown')}"))
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
                                 tracking_longitude, tracking_location_updated_at, fare, original_fare,
                                 discount_percentage, discount_amount, final_fare, fare_source
        """,
        (payload.latitude, payload.longitude, trip_id),
    )
    return _public_trip(cur.fetchone())


@router.get("/tracking/{tracking_token}", response_model=PublicTrip)
def public_tracking(tracking_token: str, cur=Depends(get_cursor_dep)):
    """Vista pública mínima para el pasajero; solo acepta el token privado del viaje."""
    cur.execute(
        """
        SELECT id, origen, destino, status, created_at, tracking_driver_name, tracking_plate,
               tracking_vehicle_model, tracking_vehicle_color, tracking_latitude,
                              tracking_longitude, tracking_location_updated_at, fare, original_fare,
                              discount_percentage, discount_amount, final_fare, fare_source
        FROM livi_trips
        WHERE tracking_token = %s
          AND created_at > NOW() - (%s * INTERVAL '1 hour')
        """,
        (tracking_token, PUBLIC_TRACKING_TOKEN_TTL_HOURS),
    )
    trip = cur.fetchone()
    if not trip:
        raise HTTPException(status_code=404, detail="Seguimiento no encontrado")
    return _public_trip(trip)


@router.get("/tracking/{tracking_token}/route")
def public_tracking_route(
    tracking_token: str,
    phase: str = Query("pickup", pattern="^(pickup|trip)$"),
    cur=Depends(get_cursor_dep),
):
    """Trazado vial público del viaje; autoriza exclusivamente el token privado."""
    cur.execute(
        """SELECT t.id, t.origen, t.destino, t.status,
                  t.origen_latitude, t.origen_longitude,
                  t.destino_latitude, t.destino_longitude,
                  a.current_latitude, a.current_longitude
           FROM livi_trips t
           LEFT JOIN livre_driver_accounts a ON a.user_id = t.assigned_driver_user_id
           WHERE t.tracking_token = %s
             AND t.created_at > NOW() - (%s * INTERVAL '1 hour')""",
        (tracking_token, PUBLIC_TRACKING_TOKEN_TTL_HOURS),
    )
    trip = cur.fetchone()
    if not trip:
        raise HTTPException(status_code=404, detail="Seguimiento no encontrado")
    if trip["status"] not in {"assigned", "arriving", "in_progress"}:
        raise HTTPException(status_code=409, detail="El viaje todavía no tiene una ruta activa")

    if phase == "pickup":
        origin = {"latitude": trip["current_latitude"], "longitude": trip["current_longitude"]}
        destination = {"latitude": trip["origen_latitude"], "longitude": trip["origen_longitude"]}
        address_fallbacks = (None, trip["origen"])
    else:
        origin = {"latitude": trip["origen_latitude"], "longitude": trip["origen_longitude"]}
        destination = {"latitude": trip["destino_latitude"], "longitude": trip["destino_longitude"]}
        address_fallbacks = (trip["origen"], trip["destino"])

    points = [origin, destination]
    for index, point in enumerate(points):
        if any(value is None for value in point.values()):
            address = address_fallbacks[index]
            if not address:
                raise HTTPException(status_code=409, detail="La ruta todavía no tiene coordenadas suficientes")
            try:
                points[index] = geocode_address(address)
            except GeocodingError as exc:
                raise HTTPException(status_code=409, detail="No se pudo ubicar una dirección del viaje") from exc
    try:
        route = route_geometry(*points)
    except GeocodingError as exc:
        raise HTTPException(status_code=502, detail="No se pudo calcular la ruta vial") from exc
    return {"trip_id": trip["id"], "phase": phase, **route}


@router.get("/tracking/{tracking_token}/messages")
def public_trip_messages(tracking_token: str, cur=Depends(get_cursor_dep)):
    """Mensajería del pasajero, aislada del canal interno de base."""
    cur.execute(
        """SELECT id FROM livi_trips
           WHERE tracking_token = %s
             AND created_at > NOW() - (%s * INTERVAL '1 hour')""",
        (tracking_token, PUBLIC_TRACKING_TOKEN_TTL_HOURS),
    )
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
    cur.execute(
        """SELECT id FROM livi_trips
           WHERE tracking_token = %s
             AND created_at > NOW() - (%s * INTERVAL '1 hour')""",
        (tracking_token, PUBLIC_TRACKING_TOKEN_TTL_HOURS),
    )
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


def _driver_trip_or_404(cur, trip_id: int, user: dict, include_terminal: bool = False):
    """Returns a trip assigned to the authenticated driver."""
    terminal_statuses = ", 'completed', 'cancelled', 'conflict'" if include_terminal else ""
    cur.execute(
        f"""
        SELECT t.id, t.origen, t.destino, t.status, t.created_at, t.scheduled_at,
               t.tracking_driver_name, t.tracking_plate,
               t.tracking_vehicle_model, t.tracking_vehicle_color,
               t.tracking_latitude, t.tracking_longitude,
               t.tracking_location_updated_at,
               COALESCE(t.service_type_id, NULL)::integer AS service_type_id,
               COALESCE(t.service_type_name, CASE t.service_type_id
                   WHEN '1' THEN 'Premium'
                   WHEN '2' THEN 'Ahorro'
                   ELSE NULL
               END) AS service_type_name,
               t.fare AS fare,
               t.original_fare, t.discount_percentage, t.discount_amount,
               t.final_fare, t.fare_source, t.whatsapp_user
        FROM livi_trips t
        JOIN livre_driver_accounts a ON a.user_id = t.assigned_driver_user_id
        WHERE t.id = %s AND a.user_id = %s AND a.active = TRUE
          AND t.status IN ('assigned', 'arriving', 'in_progress'{terminal_statuses})
        """,
        (trip_id, user["id"]),
    )
    trip = cur.fetchone()
    if not trip:
        raise HTTPException(status_code=404, detail="Viaje no asignado a este conductor")
    return trip


def _driver_fare(trip) -> float | None:
    """Returns the driver's amount using service type as the commercial authority.

    A persisted commercial snapshot is trusted for ordinary services. Ahorro is
    deliberately authoritative over stale snapshots because its contract is
    always 0% commission. Completed legacy trips without a snapshot keep fare
    unchanged and are never recalculated.
    """
    if trip.get("status") == "completed" and trip.get("final_fare") is None:
        historical_fare = trip.get("fare") if trip.get("fare") is not None else trip.get("original_fare")
        return float(historical_fare) if historical_fare is not None else None

    gross = trip.get("original_fare") if trip.get("original_fare") is not None else trip.get("fare")
    if gross is None:
        return None
    if str(trip.get("service_type_id")) == "2":
        return float(gross)
    if trip.get("final_fare") is not None:
        return float(trip["final_fare"])
    return float(
        (Decimal(str(gross)) * Decimal("0.85")).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    )


def _driver_trip(trip) -> DriverTrip:
    return DriverTrip(
        id=trip["id"],
        origin=trip["origen"],
        destination=trip["destino"],
        status=trip["status"],
        created_at=trip["created_at"],
        driver_name=trip["tracking_driver_name"],
        plate=trip["tracking_plate"],
        vehicle_model=trip["tracking_vehicle_model"],
        vehicle_color=trip["tracking_vehicle_color"],
        latitude=trip["tracking_latitude"],
        longitude=trip["tracking_longitude"],
        location_updated_at=trip["tracking_location_updated_at"],
        service_type_id=trip["service_type_id"],
        service_type_name=trip["service_type_name"],
        fare=(
            float(trip["original_fare"])
            if trip["original_fare"] is not None
            else (float(trip["fare"]) if trip["fare"] is not None else None)
        ),
        driver_fare=_driver_fare(trip),
        original_fare=float(trip["original_fare"]) if trip["original_fare"] is not None else None,
        discount_percentage=(
            float(trip["discount_percentage"])
            if trip["discount_percentage"] is not None
            else None
        ),
        discount_amount=(
            float(trip["discount_amount"])
            if trip["discount_amount"] is not None
            else None
        ),
        final_fare=float(trip["final_fare"]) if trip["final_fare"] is not None else None,
        fare_source=trip["fare_source"],
        scheduled_at=trip.get("scheduled_at"),
    )


def _history_bounds(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    if date_to < date_from:
        raise HTTPException(status_code=422, detail="date_to no puede ser anterior a date_from")
    if (date_to - date_from).days > 366:
        raise HTTPException(status_code=422, detail="El período máximo es de 366 días")
    return (
        datetime.combine(date_from, datetime.min.time()),
        datetime.combine(date_to + timedelta(days=1), datetime.min.time()),
    )


def _history_response(cur, user_id: str, date_from: date, date_to: date) -> dict:
    start, end = _history_bounds(date_from, date_to)
    cur.execute(
        """
        SELECT t.id, t.origen, t.destino, t.status,
               COALESCE(t.updated_at, t.created_at) AS completed_at,
               t.fare AS fare,
               t.service_type_id,
               t.original_fare, t.discount_percentage, t.discount_amount,
               t.final_fare,
               CASE WHEN t.final_fare IS NOT NULL THEN 'commercial' ELSE t.fare_source END AS fare_source
        FROM livi_trips t
        WHERE t.assigned_driver_user_id = %s
          AND t.status = 'completed'
          AND COALESCE(t.updated_at, t.created_at) >= %s
          AND COALESCE(t.updated_at, t.created_at) < %s
        ORDER BY COALESCE(t.updated_at, t.created_at) DESC, t.id DESC
        """,
        (user_id, start, end),
    )
    items = cur.fetchall()
    gross_values = [
        row["original_fare"] if row["original_fare"] is not None else row["fare"]
        for row in items
    ]
    total_revenue = sum((value for value in gross_values if value is not None), Decimal("0"))
    driver_values = [_driver_fare(row) for row in items]
    driver_values_decimal = [Decimal(str(value)) for value in driver_values if value is not None]
    driver_revenue = (
        sum(driver_values_decimal, Decimal("0"))
        if items and len(driver_values_decimal) == len(items)
        else None
    )
    commission = total_revenue - driver_revenue if driver_revenue is not None else None
    return {
        "total": len(items),
        "total_revenue": total_revenue,
        "driver_revenue": driver_revenue,
        "commission": commission,
        "items": [
            {
                "id": row["id"], "origin": row["origen"], "destination": row["destino"],
                "status": row["status"], "completed_at": row["completed_at"],
                "fare": row["fare"], "driver_fare": _driver_fare(row),
                "service_type_id": row["service_type_id"],
                "original_fare": row["original_fare"],
                "discount_percentage": row["discount_percentage"],
                "discount_amount": row["discount_amount"],
                "final_fare": row["final_fare"], "fare_source": row["fare_source"],
            }
            for row in items
        ],
    }


@router.get("/driver/history", response_model=DriverHistoryResponse)
def driver_history(
    date_from: date = Query(...),
    date_to: date = Query(...),
    cur=Depends(get_cursor_dep), user: dict = Depends(require_driver),
):
    return _history_response(cur, user["id"], date_from, date_to)


@router.get("/driver/shift-closures", response_model=list[DriverShiftClosure])
def driver_shift_closures(cur=Depends(get_cursor_dep), user: dict = Depends(require_driver)):
    cur.execute(
        """SELECT id, closure_date, trip_count, total_revenue, driver_revenue,
                  commission, created_at
           FROM livre_driver_shift_closures
           WHERE driver_user_id = %s
           ORDER BY closure_date DESC, id DESC
           LIMIT 100""",
        (user["id"],),
    )
    return cur.fetchall()


@router.post("/driver/shift-closures", response_model=DriverShiftClosure, status_code=201)
def create_driver_shift_closure(
    payload: DriverShiftClosureCreate,
    cur=Depends(get_cursor_dep), user: dict = Depends(require_driver),
):
    history = _history_response(cur, user["id"], payload.closure_date, payload.closure_date)
    cur.execute(
        """INSERT INTO livre_driver_shift_closures
               (driver_user_id, closure_date, trip_count, total_revenue, driver_revenue, commission)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT (driver_user_id, closure_date) DO NOTHING
           RETURNING id, closure_date, trip_count, total_revenue, driver_revenue, commission, created_at""",
        (user["id"], payload.closure_date, history["total"], history["total_revenue"],
         history["driver_revenue"], history["commission"]),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=409, detail="Este turno ya está cerrado")
    return row


def _pdf_text(value: object) -> bytes:
    """Encode a short line using PDF's built-in WinAnsi font safely."""
    text = str(value).encode("cp1252", errors="replace")
    return b"(" + text.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"


def _daily_closure_pdf(row: dict) -> bytes:
    lines = [
        "LIVRE - RESUMEN DIARIO",
        f"Fecha: {row['closure_date']}",
        f"Viajes: {row['trip_count']}",
        f"Total facturado: {row['total_revenue']}",
        f"Importe del conductor: {row['driver_revenue']}",
        f"Comisiones: {row['commission']}",
    ]
    commands = [b"BT", b"/F1 16 Tf", b"50 740 Td"]
    for index, line in enumerate(lines):
        if index:
            commands.append(b"0 -28 Td")
        commands.extend([_pdf_text(line), b"Tj"])
    commands.append(b"ET")
    content = b" ".join(commands) + b"\n"
    objects = [
        b"<</Type /Catalog /Pages 2 0 R>>",
        b"<</Type /Pages /Count 1 /Kids [3 0 R]>>",
        b"<</Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R>>",
        b"<</Length " + str(len(content)).encode() + b">>\nstream\n" + content + b"endstream",
        b"<</Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding>>",
    ]
    pdf = bytearray(b"%PDF-1.4\n%Livre\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode())
    pdf.extend(
        f"trailer\n<</Size {len(objects) + 1} /Root 1 0 R>>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(pdf)


@router.get("/driver/shift-closures/{closure_id}/pdf")
def driver_shift_closure_pdf(
    closure_id: str, cur=Depends(get_cursor_dep), user: dict = Depends(require_driver),
):
    cur.execute(
        """SELECT id, closure_date, trip_count, total_revenue, driver_revenue, commission
           FROM livre_driver_shift_closures
           WHERE id = %s AND driver_user_id = %s""",
        (closure_id, user["id"]),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Cierre no encontrado")
    pdf = _daily_closure_pdf(row)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="livre-cierre-{closure_id}.pdf"'},
    )


@router.get("/driver/operational-status", response_model=DriverOperationalStatusResponse)
def get_driver_operational_status(
    cur=Depends(get_cursor_dep), user: dict = Depends(require_driver)
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
    cur=Depends(get_cursor_dep), user: dict = Depends(require_driver)
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
def driver_trips(cur=Depends(get_cursor_dep), user: dict = Depends(require_driver)):
    """Lists open trips available to this conductor and trips already assigned to them."""
    # Un viaje que agotó la ventana de búsqueda pasa a Perdidas sólo cuando
    # tampoco existe ningún conductor disponible en toda la flota.
    timeout_minutes = max(1, int(os.getenv("TRIP_SEARCH_TIMEOUT_MINUTES", "10")))
    cur.execute(
        """
        UPDATE livi_trips
           SET status = 'searching', updated_at = NOW()
         WHERE status = 'scheduled'
           AND scheduled_at IS NOT NULL
           AND scheduled_at <= NOW()
        """
    )
    cur.execute(
        """
        UPDATE livi_trips
           SET status = 'lost', updated_at = NOW()
         WHERE status IN ('pending', 'searching')
           AND assigned_driver_user_id IS NULL
           AND created_at < NOW() - (%s || ' minutes')::interval
           AND NOT EXISTS (
               SELECT 1 FROM livre_driver_accounts
                WHERE operational_status = 'available'
                  AND (active = TRUE OR location_updated_at >= NOW() - INTERVAL '2 minutes')
           )
        """,
        (str(timeout_minutes),),
    )
    cur.execute(
        """
        SELECT t.id, t.origen, t.destino, t.status, t.created_at, t.scheduled_at,
               t.tracking_driver_name, t.tracking_plate,
               t.tracking_vehicle_model, t.tracking_vehicle_color,
               t.tracking_latitude, t.tracking_longitude,
               t.tracking_location_updated_at,
               COALESCE(t.service_type_id, NULL)::integer AS service_type_id,
               COALESCE(t.service_type_name, CASE t.service_type_id
                   WHEN '1' THEN 'Premium'
                   WHEN '2' THEN 'Ahorro'
                   ELSE NULL
               END) AS service_type_name,
               t.fare AS fare,
               t.original_fare, t.discount_percentage, t.discount_amount,
               t.final_fare, t.fare_source, t.whatsapp_user
        FROM livi_trips t
        WHERE (
            (
                EXISTS (
                    SELECT 1 FROM livre_driver_accounts active_driver
                    WHERE active_driver.user_id = %s
                      AND active_driver.operational_status = 'available'
                      AND (active_driver.active = TRUE
                           OR active_driver.location_updated_at >= NOW() - INTERVAL '2 minutes')
                )
                AND t.status IN ('scheduled', 'searching')
                AND t.assigned_driver_user_id IS NULL
            )
            OR (
                t.assigned_driver_user_id = %s
                AND t.status IN ('assigned', 'arriving', 'in_progress')
            )
        )
        ORDER BY t.created_at DESC
        """,
        (user["id"], user["id"]),
    )
    return [_driver_trip(row) for row in cur.fetchall()]


@router.get("/driver/trips/{trip_id}/route")
def driver_trip_route(
    trip_id: int,
    phase: str = Query("pickup", pattern="^(pickup|trip)$"),
    cur=Depends(get_cursor_dep),
    user: dict = Depends(require_driver),
):
    """Calcula la ruta vial del conductor sin exponer el proveedor al cliente."""
    trip = _driver_trip_or_404(cur, trip_id, user)
    cur.execute(
        """
        SELECT t.origen_latitude, t.origen_longitude,
               t.destino_latitude, t.destino_longitude,
               a.current_latitude, a.current_longitude
        FROM livi_trips t
        JOIN livre_driver_accounts a ON a.user_id = t.assigned_driver_user_id
        WHERE t.id = %s AND a.user_id = %s AND a.active = TRUE
          AND t.status IN ('assigned', 'arriving', 'in_progress')
        """,
        (trip_id, user["id"]),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Viaje no asignado a este conductor")

    if phase == "pickup":
        origin = {"latitude": row["current_latitude"], "longitude": row["current_longitude"]}
        destination = {"latitude": row["origen_latitude"], "longitude": row["origen_longitude"]}
    else:
        origin = {"latitude": row["origen_latitude"], "longitude": row["origen_longitude"]}
        destination = {"latitude": row["destino_latitude"], "longitude": row["destino_longitude"]}

    # El CRM puede resolver la ruta desde las direcciones aunque el viaje
    # todavía no tenga las coordenadas persistidas. Repetimos ese contrato
    # aquí; nunca inventamos una posición ni hacemos geocoding en el cliente.
    address_fallbacks = {
        "pickup": (None, trip["origen"]),
        "trip": (trip["origen"], trip["destino"]),
    }[phase]
    points = [origin, destination]
    for index, point in enumerate(points):
        if any(value is None for value in point.values()):
            address = address_fallbacks[index]
            if not address:
                raise HTTPException(status_code=409, detail="El viaje todavía no tiene coordenadas suficientes para trazar la ruta")
            try:
                points[index] = geocode_address(address)
            except GeocodingError as exc:
                raise HTTPException(status_code=409, detail="No se pudo ubicar una dirección del viaje para trazar la ruta") from exc
    origin, destination = points
    try:
        route = route_geometry(origin, destination)
    except GeocodingError as exc:
        raise HTTPException(status_code=502, detail="No se pudo calcular la ruta vial") from exc
    return {"trip_id": trip_id, "phase": phase, **route}


@router.get("/driver/trips/{trip_id}/trace")
def driver_trip_trace(
    trip_id: int,
    cur=Depends(get_cursor_dep),
    user: dict = Depends(require_driver),
):
    """Devuelve únicamente el historial GPS del viaje del conductor."""
    _driver_trip_or_404(cur, trip_id, user)
    cur.execute(
        """SELECT latitude, longitude, recorded_at
           FROM livre_trip_location_history
           WHERE trip_id = %s AND driver_user_id = %s
             AND latitude IS NOT NULL AND longitude IS NOT NULL
           ORDER BY recorded_at ASC""",
        (trip_id, user["id"]),
    )
    return {
        "trip_id": trip_id,
        "points": [
            {
                "latitude": float(row["latitude"]),
                "longitude": float(row["longitude"]),
                "recorded_at": row["recorded_at"].isoformat() if row["recorded_at"] else None,
            }
            for row in cur.fetchall()
        ],
    }


@router.get("/driver/trips/{trip_id}/messages")
def driver_trip_messages(
    trip_id: int,
    cur=Depends(get_cursor_dep),
    user: dict = Depends(require_driver),
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
    user: dict = Depends(require_driver),
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
    user: dict = Depends(require_driver),
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
    user: dict = Depends(require_driver),
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
    user: dict = Depends(require_driver),
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
    user: dict = Depends(require_driver),
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


@router.post("/driver/trips/{trip_id}/reject", response_model=DriverTrip)
def driver_reject_trip(
    trip_id: int,
    cur=Depends(get_cursor_dep),
    user: dict = Depends(require_driver),
):
    """Libera un viaje rechazado para que pueda tomarlo otro conductor."""
    trip = _driver_trip_or_404(cur, trip_id, user)
    if trip["status"] != "assigned":
        raise HTTPException(status_code=409, detail="Sólo se puede rechazar un viaje pendiente")
    cur.execute(
        """UPDATE livi_trips
           SET status = CASE
                    WHEN scheduled_at IS NOT NULL AND scheduled_at > NOW() THEN 'scheduled'
                    ELSE 'searching'
               END,
               assigned_driver_user_id = NULL,
               tracking_driver_name = NULL,
               updated_at = NOW()
           WHERE id = %s AND assigned_driver_user_id = %s AND status = 'assigned'
           RETURNING id, status""",
        (trip_id, user["id"]),
    )
    rejected = cur.fetchone()
    if not rejected:
        raise HTTPException(status_code=409, detail="El viaje ya cambió de estado")
    record_trip_event(
        cur, trip_id=trip_id, actor_user_id=user["id"], actor_role="driver",
        event_type="trip_rejected", previous_status="assigned", new_status=rejected["status"],
    )
    cur.execute("SELECT * FROM livi_trips WHERE id = %s", (trip_id,))
    rejected_trip = cur.fetchone()
    return _driver_trip(rejected_trip)


@router.post("/driver/trips/{trip_id}/status", response_model=DriverTrip)
def driver_update_status(
    trip_id: int,
    payload: StatusUpdate,
    cur=Depends(get_cursor_dep),
    user: dict = Depends(require_driver),
):
    """Claims an assigned trip for the first conductor who takes it."""
    previous_status = None
    try:
        trip = _driver_trip_or_404(cur, trip_id, user)
    except HTTPException:
        if payload.status not in {"assigned", "arriving"}:
            raise
        cur.execute("SELECT status FROM livi_trips WHERE id = %s FOR UPDATE", (trip_id,))
        claim = cur.fetchone()
        if not claim or claim["status"] not in {"scheduled", "searching", "assigned"}:
            raise HTTPException(status_code=409, detail="El viaje ya fue tomado por otro conductor")
        previous_status = claim["status"]
        claimed_status = "assigned" if previous_status == "scheduled" else payload.status
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
              AND t.status IN ('searching', 'assigned')
                              AND t.assigned_driver_user_id IS NULL
              AND a.user_id = %s
              AND a.operational_status = 'available'
              AND (a.active = TRUE
                   OR a.location_updated_at >= NOW() - INTERVAL '2 minutes')
            RETURNING t.id
            """,
            (user["id"], claimed_status, trip_id, user["id"]),
        )
        if not cur.fetchone():
            raise HTTPException(status_code=409, detail="El viaje ya fue tomado por otro conductor")
        trip = _driver_trip_or_404(cur, trip_id, user)
    if previous_status is None:
        previous_status = trip["status"]
    allowed_transitions = {
        "assigned": {"arriving", "cancelled", "conflict"},
        "arriving": {"in_progress", "cancelled", "conflict"},
        "in_progress": {"completed", "cancelled", "conflict"},
    }
    if previous_status in {"scheduled", "searching"} and payload.status in {"assigned", "arriving"}:
        pass
    elif payload.status not in allowed_transitions.get(previous_status, set()):
        raise HTTPException(status_code=409, detail="El viaje ya no permite esa transición")
    if payload.status == "conflict" and not payload.reason:
        raise HTTPException(status_code=422, detail="Indicá el motivo del conflicto")
    if previous_status != "assigned" or payload.status != "arriving":
        cur.execute(
            """
            UPDATE livi_trips
               SET status = %s, conflict_reason = %s, updated_at = NOW()
            WHERE id = %s AND assigned_driver_user_id = %s
            """,
            (payload.status, payload.reason, trip_id, user["id"]),
        )
    elif trip["status"] == "assigned":
        cur.execute(
            """
            UPDATE livi_trips SET status = 'arriving', conflict_reason = NULL, updated_at = NOW()
            WHERE id = %s AND assigned_driver_user_id = %s
            """,
            (trip_id, user["id"]),
        )
    record_trip_event(
        cur, trip_id=trip_id, actor_user_id=user["id"], actor_role="driver",
        event_type="status_changed", previous_status=previous_status,
        new_status=payload.status,
    )
    if payload.status in ("cancelled", "conflict", "lost"):
        # Un viaje que no termina correctamente no deja historial GPS persistido.
        cur.execute(
            "DELETE FROM livre_trip_location_history WHERE trip_id = %s",
            (trip_id,),
        )
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
    return _driver_trip(_driver_trip_or_404(cur, trip_id, user, include_terminal=True))


@router.post("/driver/location")
def driver_update_live_location(
    payload: LocationUpdate,
    cur=Depends(get_cursor_dep),
    user: dict = Depends(require_driver),
):
    """Stores the driver's latest GPS position even without an active trip."""
    _require_current_driver_device(cur, user, payload.device_id)
    cur.execute(
        """
        UPDATE livre_driver_accounts
        SET current_latitude = %s,
            current_longitude = %s,
            location_updated_at = NOW(),
            updated_at = NOW()
        WHERE user_id = %s AND active = TRUE
        RETURNING user_id, location_updated_at
        """,
        (payload.latitude, payload.longitude, user["id"]),
    )
    row = cur.fetchone()
    if not row:
        log.warning(
            "driver_location_rejected event=inactive_driver driver_user_id=%s device_id=%s",
            user.get("id"),
            payload.device_id,
        )
        raise HTTPException(status_code=403, detail="La cuenta de conductor no está activa")
    log.info(
        "driver_location_received event=heartbeat driver_user_id=%s device_id=%s recorded_at=%s",
        user.get("id"),
        payload.device_id,
        row["location_updated_at"],
    )
    # Una muestra por ventana de 30 minutos permite medir suspensiones reales
    # sin guardar coordenadas ni inundar los logs con cada pulso.
    cur.execute(
        """
        INSERT INTO livre_driver_heartbeat_diagnostics
            (driver_user_id, device_id, bucket_start, last_received_at)
        VALUES (
            %s, %s,
            to_timestamp(floor(extract(epoch from NOW()) / 1800) * 1800),
            NOW()
        )
        ON CONFLICT (driver_user_id, device_id, bucket_start)
        DO UPDATE SET last_received_at = EXCLUDED.last_received_at
        """,
        (user["id"], payload.device_id),
    )
    return {"status": "updated", "location_updated_at": row["location_updated_at"]}


@router.delete("/driver/location")
def driver_clear_live_location(
    cur=Depends(get_cursor_dep),
    user: dict = Depends(require_auth),
    x_device_id: str | None = Header(default=None, alias="X-Device-ID"),
):
    """Clears only the authenticated driver's current device location on logout."""
    _require_current_driver_device(cur, user, x_device_id)
    cur.execute(
        """UPDATE livre_driver_accounts
           SET current_latitude = NULL,
               current_longitude = NULL,
               location_updated_at = NULL,
               updated_at = NOW()
           WHERE user_id = %s AND active = TRUE
           RETURNING user_id""",
        (user["id"],),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=403, detail="La cuenta de conductor no está activa")
    cur.execute(
        """UPDATE livre_driver_device_sessions
           SET active = FALSE, revoked_at = NOW(), last_seen_at = NOW()
           WHERE user_id = %s AND device_id = %s AND active = TRUE""",
        (user["id"], x_device_id),
    )
    return {"status": "cleared"}


@router.post("/driver/trips/{trip_id}/location", response_model=DriverTrip)
def driver_update_location(
    trip_id: int,
    payload: LocationUpdate,
    cur=Depends(get_cursor_dep),
    user: dict = Depends(require_driver),
):
    """Stores GPS only for a trip assigned to the authenticated driver."""
    _require_current_driver_device(cur, user, payload.device_id)
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
    # No se persisten consultas ni viajes rechazados. El historial empieza
    # cuando el viaje ya fue aceptado por el conductor.
    cur.execute(
        """
        INSERT INTO livre_trip_location_history
            (trip_id, driver_user_id, latitude, longitude)
        SELECT id, assigned_driver_user_id, %s, %s
        FROM livi_trips
        WHERE id = %s
          AND assigned_driver_user_id = %s
          AND status IN ('assigned', 'arriving', 'in_progress')
        """,
        (payload.latitude, payload.longitude, trip_id, user["id"]),
    )
    return _driver_trip(_driver_trip_or_404(cur, trip_id, user))
