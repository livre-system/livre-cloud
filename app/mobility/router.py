"""API base para las apps Livre de pasajeros y conductores.

Este módulo es la frontera propia de Livre Cloud. No expone MAGIIS directamente.
"""

import os
import secrets
import uuid
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException

from app.core.database import get_cursor_dep
from app.core.jwt import require_auth
from app.mobility.schemas import (
    DriverTrip,
    LocationUpdate,
    PublicTrip,
    StatusUpdate,
    TripRequest,
    TripRequestResponse,
)

router = APIRouter(prefix="/mobility", tags=["mobility"])


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

    cur.execute(
        """
        SELECT u.id, u.name
        FROM livre_users u
        JOIN livre_driver_accounts a ON a.user_id = u.id
        WHERE a.active = TRUE
        ORDER BY a.created_at ASC, u.created_at ASC
        LIMIT 1
        """
    )
    driver = cur.fetchone()
    if not driver:
        raise HTTPException(status_code=503, detail="No hay un conductor Livre disponible")

    tracking_token = secrets.token_urlsafe(32)
    conversation_id = f"web_{uuid.uuid4()}"
    cur.execute(
        """
        INSERT INTO livi_trips
            (conversation_id, origen, destino, whatsapp_user, tracking_token,
             assigned_driver_user_id, tracking_driver_name, status, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, 'assigned', NOW(), NOW())
        RETURNING id, tracking_token, status
        """,
        (conversation_id, origin, destination, payload.whatsapp_user.strip(), tracking_token, driver["id"], driver["name"]),
    )
    trip = cur.fetchone()
    passenger_url = os.getenv("PASSENGER_APP_URL", "https://livre-pasajeros.vercel.app")
    tracking_url = f"{passenger_url.rstrip('/')}?token={quote(tracking_token)}"
    return TripRequestResponse(
        trip_id=trip["id"],
        tracking_token=trip["tracking_token"],
        tracking_url=tracking_url,
        status=trip["status"],
        message="Pedido recibido y asignado a un conductor Livre.",
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
    return _public_trip(cur.fetchone())


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


def _driver_trip_or_404(cur, trip_id: int, user: dict):
    """Returns a trip only when it is assigned to the authenticated driver."""
    cur.execute(
        """
        SELECT t.id, t.origen, t.destino, t.status,
               t.tracking_driver_name, t.tracking_plate,
               t.tracking_vehicle_model, t.tracking_vehicle_color,
               t.tracking_latitude, t.tracking_longitude,
               t.tracking_location_updated_at
        FROM livi_trips t
        JOIN livre_driver_accounts a ON a.user_id = t.assigned_driver_user_id
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
    )


@router.get("/driver/trips", response_model=list[DriverTrip])
def driver_trips(cur=Depends(get_cursor_dep), user: dict = Depends(require_auth)):
    """Lists current trips assigned to the authenticated driver."""
    cur.execute(
        """
        SELECT t.id, t.origen, t.destino, t.status,
               t.tracking_driver_name, t.tracking_plate,
               t.tracking_vehicle_model, t.tracking_vehicle_color,
               t.tracking_latitude, t.tracking_longitude,
               t.tracking_location_updated_at
        FROM livi_trips t
        JOIN livre_driver_accounts a ON a.user_id = t.assigned_driver_user_id
        WHERE a.user_id = %s AND a.active = TRUE
          AND t.status NOT IN ('completed', 'cancelled')
        ORDER BY t.created_at DESC
        """,
        (user["id"],),
    )
    return [_driver_trip(row) for row in cur.fetchall()]


@router.post("/driver/trips/{trip_id}/status", response_model=DriverTrip)
def driver_update_status(
    trip_id: int,
    payload: StatusUpdate,
    cur=Depends(get_cursor_dep),
    user: dict = Depends(require_auth),
):
    """Updates status only for a trip assigned to the authenticated driver."""
    _driver_trip_or_404(cur, trip_id, user)
    cur.execute(
        """
        UPDATE livi_trips SET status = %s, updated_at = NOW()
        WHERE id = %s
        """,
        (payload.status, trip_id),
    )
    return _driver_trip(_driver_trip_or_404(cur, trip_id, user))


@router.post("/driver/trips/{trip_id}/location", response_model=DriverTrip)
def driver_update_location(
    trip_id: int,
    payload: LocationUpdate,
    cur=Depends(get_cursor_dep),
    user: dict = Depends(require_auth),
):
    """Stores GPS only for a trip assigned to the authenticated driver."""
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
