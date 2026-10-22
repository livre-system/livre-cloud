"""API autenticada de consulta para los datos locales de Livre."""
import json
import logging
import math
import secrets
import uuid
from datetime import date, datetime, timedelta, timezone

import bcrypt
from fastapi import APIRouter, Body, Depends, Query, HTTPException
from app.core.database import get_cursor_dep
from app.core.jwt import require_auth
from app.core.operational_events import record_trip_event
from app.mobility.schemas import DriverOperationalStatusResponse, DriverOperationalStatusUpdate

router = APIRouter(prefix="/crm", tags=["crm"])
log = logging.getLogger(__name__)

_RESOURCES = {
    "passengers": "livre_crm_passengers",
    "vehicles": "livre_crm_vehicles",
    "travels": "livi_trips",
    "vehicle_owners": "livre_crm_vehicle_owners",
}

# These accounts remain in livre_users/CRM, but are not GPS/map conductors.
# Keep this list keyed by verified production user_id, not display names:
# "Perez Ezequiel" and "IBAÑEZ PUCHULO CARLOS MARTIN" are real drivers.
_HIDDEN_GPS_DRIVER_USER_IDS = ()


def _require_crm_operator(user: dict = Depends(require_auth)) -> dict:
    """Allow only explicit CRM/admin roles; preserve the existing livreuser owner."""
    roles = set(user.get("roles") or [])
    if roles and roles.intersection({"crm", "crm_admin", "admin"}):
        return user
    if user.get("id") != "livreuser":
        raise HTTPException(status_code=403, detail="Se requiere un rol de operador CRM")
    return user


def _require_crm_admin(user: dict = Depends(require_auth)) -> dict:
    roles = set(user.get("roles") or [])
    if user.get("id") == "livreuser" or roles.intersection({"crm_admin", "admin"}):
        return user
    raise HTTPException(status_code=403, detail="Se requiere un rol administrador CRM")


def _require_livreuser(cur, user: dict) -> None:
    """The first CRM administrator is the only route-history viewer for now."""
    cur.execute("SELECT username FROM livre_users WHERE id = %s", (user.get("id"),))
    row = cur.fetchone()
    if not row or row["username"] != "livreuser":
        raise HTTPException(status_code=403, detail="No tenés permiso para ver recorridos")


@router.get("/operational/outbox")
def outbox_status(
    user: dict = Depends(_require_crm_operator),
    cur=Depends(get_cursor_dep),
):
    """Estado agregado del outbox, sin exponer payloads ni errores sensibles."""
    cur.execute(
        """SELECT status, count(*) AS total,
                  min(created_at) AS oldest_created_at
             FROM livre_trip_event_outbox
            GROUP BY status
            ORDER BY status"""
    )
    rows = cur.fetchall()
    return {
        "statuses": [
            {
                "status": row["status"],
                "total": int(row["total"]),
                "oldest_created_at": row["oldest_created_at"].isoformat()
                if row["oldest_created_at"] else None,
            }
            for row in rows
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


_ALLOWED_CRM_ROLES = {"crm", "crm_admin"}


@router.get("/users/{user_id}/roles")
def list_user_roles(user_id: str, user: dict = Depends(_require_crm_admin), cur=Depends(get_cursor_dep)):
    cur.execute("SELECT id, username, name FROM livre_users WHERE id = %s", (user_id,))
    target = cur.fetchone()
    if not target:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    cur.execute("SELECT role, created_at FROM livre_user_roles WHERE user_id = %s ORDER BY role", (user_id,))
    return {"user": dict(target), "roles": [dict(row) for row in cur.fetchall()]}


@router.put("/users/{user_id}/roles")
def set_user_roles(user_id: str, payload: dict = Body(...), user: dict = Depends(_require_crm_admin), cur=Depends(get_cursor_dep)):
    requested = payload.get("roles")
    if not isinstance(requested, list) or not set(requested).issubset(_ALLOWED_CRM_ROLES):
        raise HTTPException(status_code=422, detail="roles debe ser una lista de crm o crm_admin")
    if user_id == user.get("id") and "crm_admin" not in requested and user.get("id") != "livreuser":
        raise HTTPException(status_code=409, detail="No podés quitarte tu último rol administrador")
    cur.execute("SELECT id FROM livre_users WHERE id = %s", (user_id,))
    if not cur.fetchone():
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    cur.execute("DELETE FROM livre_user_roles WHERE user_id = %s", (user_id,))
    for role in sorted(set(requested)):
        cur.execute("INSERT INTO livre_user_roles (user_id, role) VALUES (%s, %s)", (user_id, role))
    cur.execute("SELECT role, created_at FROM livre_user_roles WHERE user_id = %s ORDER BY role", (user_id,))
    return {"user_id": user_id, "roles": [dict(row) for row in cur.fetchall()]}


@router.get("/operational/metrics")
def operational_metrics(user: dict = Depends(_require_crm_operator), cur=Depends(get_cursor_dep)):
    cur.execute("SELECT status, count(*) AS total FROM livi_trips GROUP BY status ORDER BY status")
    trips = {row["status"]: int(row["total"]) for row in cur.fetchall()}
    cur.execute("""SELECT count(*) FILTER (WHERE status IN ('pending','processing')) AS pending,
                         count(*) FILTER (WHERE status = 'failed') AS failed,
                         count(*) FILTER (WHERE status = 'sent') AS sent
                    FROM livre_trip_event_outbox""")
    outbox = dict(cur.fetchone())
    cur.execute("""SELECT count(*) AS total FROM livi_trips
                    WHERE assigned_driver_user_id IS NOT NULL
                      AND (tracking_location_updated_at IS NULL OR tracking_location_updated_at < NOW() - INTERVAL '2 minutes')
                      AND status IN ('assigned','arriving','in_progress')""")
    stale_gps = int(cur.fetchone()["total"])
    return {"trips_by_status": trips, "outbox": {key: int(value or 0) for key, value in outbox.items()}, "stale_gps_trips": stale_gps, "generated_at": datetime.now(timezone.utc).isoformat()}

# Estados operativos explícitos. Los estados de error son históricos/terminales:
# no deben aparecer como "en viaje" por el solo hecho de no ser completed.
_OPEN_TRIP_STATUSES = frozenset({
    "pending", "searching", "created", "confirmed", "assigned",
    "arriving", "in_progress",
})
_TERMINAL_TRIP_STATUSES = frozenset({
    "completed", "cancelled", "magiis_error", "cancel_error",
})


def _trip_status_semantics(trip_totals: dict[tuple[str, str], int]) -> dict:
    """Classify observed statuses without treating unknown/error as open."""
    by_status = {}
    for (status, _activity_type), total in trip_totals.items():
        by_status[status] = by_status.get(status, 0) + total
    open_total = sum(by_status.get(status, 0) for status in _OPEN_TRIP_STATUSES)
    terminal_total = sum(by_status.get(status, 0) for status in _TERMINAL_TRIP_STATUSES)
    unknown_total = sum(
        total for status, total in by_status.items()
        if status not in _OPEN_TRIP_STATUSES and status not in _TERMINAL_TRIP_STATUSES
    )
    return {
        "open": open_total,
        "terminal": terminal_total,
        "unknown": unknown_total,
        "by_status": by_status,
        "open_statuses": sorted(_OPEN_TRIP_STATUSES),
        "terminal_statuses": sorted(_TERMINAL_TRIP_STATUSES),
    }


def _local_page(cur, table: str, page: int, size: int, q: str = ""):
    """Lista entidades propias de Livre; nunca abre una sesión externa."""
    if table == "livre_crm_passengers":
        search = "(first_name ILIKE %s OR last_name ILIKE %s OR phone ILIKE %s OR COALESCE(email, '') ILIKE %s)"
        params = [f"%{q.strip()}%"] * 4 if q.strip() else []
        where = f" WHERE {search}" if params else ""
        cur.execute(f"SELECT count(*) AS total FROM {table}{where}", params)
        total = int(cur.fetchone()["total"])
        cur.execute(f"SELECT id, first_name, last_name, email, phone, country, raw_payload FROM {table}{where} ORDER BY id DESC LIMIT %s OFFSET %s", params + [size, (page - 1) * size])
        items = [{"id": r["id"], "raw_payload": {**(r["raw_payload"] or {}), "firstName": r["first_name"], "lastName": r["last_name"], "email": r["email"], "phone": r["phone"], "country": r["country"]}} for r in cur.fetchall()]
    elif table == "livre_crm_vehicle_owners":
        search = "(first_name ILIKE %s OR last_name ILIKE %s OR phone ILIKE %s OR COALESCE(email, '') ILIKE %s OR COALESCE(identification, '') ILIKE %s)"
        params = [f"%{q.strip()}%"] * 5 if q.strip() else []
        where = f" WHERE {search}" if params else ""
        cur.execute(f"SELECT count(*) AS total FROM {table}{where}", params)
        total = int(cur.fetchone()["total"])
        cur.execute(f"SELECT id, first_name, last_name, identification, email, phone, city, province, raw_payload FROM {table}{where} ORDER BY id DESC LIMIT %s OFFSET %s", params + [size, (page - 1) * size])
        items = [{"id": r["id"], "raw_payload": {**(r["raw_payload"] or {}), "firstName": r["first_name"], "lastName": r["last_name"], "idNumber": r["identification"], "contactEmail": r["email"], "contactPhone": r["phone"], "city": r["city"], "province": r["province"]}} for r in cur.fetchall()]
    else:
        raise HTTPException(status_code=500, detail="Recurso local sin adaptador")
    return {"source": "livre-cloud", "items": items, "page": page, "size": size, "total": total}


def _local_driver_page(cur, page: int, size: int, q: str = "", vehicle: str = "", operability: str = ""):
    """Lista conductores desde la identidad y estado propios de Livre."""
    filters = ["a.active = TRUE"]
    params = []
    if q.strip():
        term = "%" + q.strip() + "%"
        filters.append("(u.name ILIKE %s OR u.username ILIKE %s OR u.email ILIKE %s OR a.user_id::text ILIKE %s)")
        params.extend([term, term, term, term])
    if vehicle.strip():
        filters.append("EXISTS (SELECT 1 FROM livre_driver_vehicles dv WHERE dv.driver_user_id = a.user_id AND dv.vehicle_label ILIKE %s)")
        params.append("%" + vehicle.strip() + "%")
    if operability.strip():
        filters.append("a.operational_status = %s")
        params.append(operability.strip())
    where = " WHERE " + " AND ".join(filters)
    cur.execute(f"SELECT count(*) AS total FROM livre_driver_accounts a JOIN livre_users u ON u.id = a.user_id{where}", params)
    total = int(cur.fetchone()["total"])
    cur.execute(
        f"""SELECT a.user_id AS id, u.name, u.username, u.email, a.operational_status,
                   a.active, a.current_latitude, a.current_longitude, a.location_updated_at,
                   COALESCE(v.vehicle_labels, ARRAY[]::text[]) AS vehicle_labels
              FROM livre_driver_accounts a
              JOIN livre_users u ON u.id = a.user_id
              LEFT JOIN LATERAL (
                  SELECT ARRAY_AGG(DISTINCT vehicle_label ORDER BY vehicle_label)
                         FILTER (WHERE NULLIF(trim(vehicle_label), '') IS NOT NULL) AS vehicle_labels
                    FROM livre_driver_vehicles
                   WHERE driver_user_id = a.user_id
              ) v ON TRUE{where}
             ORDER BY u.name NULLS LAST, a.user_id
             LIMIT %s OFFSET %s""",
        params + [size, (page - 1) * size],
    )
    items = []
    for row in cur.fetchall():
        vehicles = row["vehicle_labels"] or []
        items.append({
            "id": row["id"], "driverUserId": row["id"], "driverName": row["name"] or row["username"],
            "email": row["email"], "operationalStatus": row["operational_status"],
            "active": row["active"], "vehicles": vehicles,
            "vehicle": {"plate": ", ".join(vehicles) if vehicles else None},
            "vehicleDescription": ", ".join(vehicles) if vehicles else None,
            "lastSee": row["location_updated_at"].isoformat() if row["location_updated_at"] else None,
            "source": "livre-cloud",
        })
    return {"source": "livre-cloud", "items": items, "page": page, "size": size, "total": total}


def _local_vehicle_page(cur, page: int, size: int, q: str = ""):
    """Lista únicamente vehículos asociados en Livre; no inventa atributos."""
    filters = ["NULLIF(trim(vehicle_label), '') IS NOT NULL"]
    params = []
    if q.strip():
        filters.append("vehicle_label ILIKE %s")
        params.append("%" + q.strip() + "%")
    where = " WHERE " + " AND ".join(filters)
    base = "livre_driver_vehicles"
    cur.execute(f"SELECT count(*) AS total FROM (SELECT vehicle_label FROM {base}{where} GROUP BY vehicle_label) vehicles", params)
    total = int(cur.fetchone()["total"])
    cur.execute(
        f"""SELECT vehicle_label AS id, vehicle_label AS plate, count(*) AS assigned_drivers
              FROM {base}{where}
             GROUP BY vehicle_label ORDER BY vehicle_label
             LIMIT %s OFFSET %s""",
        params + [size, (page - 1) * size],
    )
    items = [{"id": row["id"], "plate": row["plate"], "assignedDrivers": int(row["assigned_drivers"]),
              "source": "livre-cloud", "dataQuality": {"model": "unavailable", "color": "unavailable"}}
             for row in cur.fetchall()]
    return {"source": "livre-cloud", "items": items, "page": page, "size": size, "total": total}


def _trip_distribution(cur):
    """Demanda global de viajes creados, sin filtrar por estado."""
    cur.execute(
        """
        WITH hours AS (
            SELECT generate_series(
                date_trunc('hour', CURRENT_TIMESTAMP) - INTERVAL '23 hours',
                date_trunc('hour', CURRENT_TIMESTAMP), INTERVAL '1 hour'
            ) AS bucket
        )
        SELECT to_char(hours.bucket, 'YYYY-MM-DD HH24:00') AS label,
               count(t.id)::int AS total
          FROM hours
          LEFT JOIN livi_trips t
            ON t.created_at >= hours.bucket
           AND t.created_at < hours.bucket + INTERVAL '1 hour'
         GROUP BY hours.bucket
         ORDER BY hours.bucket
        """
    )
    hourly = [dict(row) for row in cur.fetchall()]
    cur.execute(
        """
        WITH days AS (
            SELECT generate_series(
                CURRENT_DATE - INTERVAL '14 days', CURRENT_DATE, INTERVAL '1 day'
            )::date AS bucket
        )
        SELECT to_char(days.bucket, 'YYYY-MM-DD') AS label,
               count(t.id)::int AS total
          FROM days
          LEFT JOIN livi_trips t ON t.created_at::date = days.bucket
         GROUP BY days.bucket
         ORDER BY days.bucket
        """
    )
    daily = [dict(row) for row in cur.fetchall()]
    return {"hourly": hourly, "daily": daily, "scope": "all_trips", "basis": "created_at"}


def _local_trip_page(
    cur, page: int, size: int, q: str = "", state: str = "",
    date_from: str = "", date_to: str = "",
):
    """Lista viajes de Livre, aceptando los estados agrupados del panel."""
    filters = []
    params = []
    if q.strip():
        filters.append("(origen ILIKE %s OR destino ILIKE %s OR whatsapp_user ILIKE %s)")
        term = "%" + q.strip() + "%"
        params.extend([term, term, term])
    if state.strip():
        requested = {value.strip() for value in state.split("-") if value.strip()}
        local_states = set()
        if requested & {"SEARCHING_DRIVER"}:
            # Los pedidos nuevos y los liberados por un rechazo individual
            # viven en searching; pending queda por compatibilidad histórica.
            # Algunos pedidos históricos del canal ElevenLabs/WhatsApp se
            # crean como assigned mientras siguen sin chofer asignado.
            local_states.update({"assigned", "searching", "pending"})
        if requested & {"SCHEDULED"}:
            local_states.add("scheduled")
        if requested & {"WITH_DRIVER_ASSIGNED", "RESERVED", "GOING_TO_CLIENT", "GOING_TO_DESTINATION", "CLOSING_TRAVEL"}:
            local_states.add("confirmed")
        if requested & {"IN_CONFLICT"}:
            local_states.add("conflict")
        if requested & {"CANCELED", "CANCELLED"}:
            local_states.add("cancelled")
        if requested & {"LOST"}:
            local_states.add("lost")
        if requested & {"DONE"}:
            local_states.add("done")
        if local_states:
            placeholders = ", ".join(["%s"] * len(local_states))
            filters.append(f"(status IN ({placeholders}) AND (status <> 'assigned' OR assigned_driver_user_id IS NULL))")
            params.extend(sorted(local_states))
    # The panel sends the operator's local calendar date.  Convert the
    # timestamptz before comparing so late-night requests in Argentina are
    # not pushed into the next UTC date and hidden from "Asignar".
    local_created_at = "created_at AT TIME ZONE 'America/Argentina/Buenos_Aires'"
    if date_from:
        filters.append(f"{local_created_at} >= %s::date")
        params.append(date_from)
    if date_to:
        filters.append(f"{local_created_at} < (%s::date + INTERVAL '1 day')")
        params.append(date_to)
    summary_filters = []
    summary_params = []
    if q.strip():
        summary_filters.append("(origen ILIKE %s OR destino ILIKE %s OR whatsapp_user ILIKE %s)")
        summary_params.extend(["%" + q.strip() + "%"] * 3)
    if date_from:
        summary_filters.append(f"{local_created_at} >= %s::date")
        summary_params.append(date_from)
    if date_to:
        summary_filters.append(f"{local_created_at} < (%s::date + INTERVAL '1 day')")
        summary_params.append(date_to)
    summary_where = " WHERE " + " AND ".join(summary_filters) if summary_filters else ""
    cur.execute(
        f"""SELECT status,
                   (assigned_driver_user_id IS NULL) AS unassigned,
                   count(*) AS total
              FROM livi_trips{summary_where}
             GROUP BY status, (assigned_driver_user_id IS NULL)""",
        summary_params,
    )
    status_totals = {}
    for row in cur.fetchall():
        status_totals[(row["status"], bool(row.get("unassigned", True)))] = int(row["total"])

    def total_for(*statuses, unassigned=None):
        return sum(
            total for (status, row_unassigned), total in status_totals.items()
            if status in statuses and (unassigned is None or row_unassigned == unassigned)
        )

    totals = {
        # Asignar muestra únicamente pedidos sin conductor; esto mantiene el
        # contador alineado con la consulta que alimenta la tabla.
        "toAsignQuantity": total_for("assigned", "searching", "pending", unassigned=True),
        "inProgressQuantity": total_for("confirmed", "assigned", "arriving", "in_progress", unassigned=False),
        "scheduledQuantity": total_for("scheduled"),
        "inConflictQuantity": total_for("conflict"),
        "lostQuantity": total_for("lost"),
        "cancelledQuantity": total_for("cancelled"),
        "doneQuantity": total_for("done", "completed"),
    }
    where = " WHERE " + " AND ".join(filters) if filters else ""
    cur.execute(f"SELECT count(*) AS total FROM livi_trips{where}", params)
    total = int(cur.fetchone()["total"])
    cur.execute(
        f"""SELECT id, origen, destino, whatsapp_user, status,
                   assigned_driver_user_id, tracking_driver_name,
                   tracking_plate, tracking_vehicle_model, tracking_vehicle_color,
                   created_at, updated_at
            FROM livi_trips{where}
            ORDER BY created_at DESC LIMIT %s OFFSET %s""",
        params + [size, (page - 1) * size],
    )
    items = [dict(row) for row in cur.fetchall()]
    return {
        "source": "livre", "items": items, "page": page, "size": size,
        "total": total, "totals": totals, "distribution": _trip_distribution(cur),
    }


@router.post("/trips/{trip_id}/assign")
def assign_trip_to_driver(
    trip_id: int,
    payload: dict = Body(...),
    user: dict = Depends(_require_crm_operator),
    cur=Depends(get_cursor_dep),
):
    """Asigna atómicamente un pedido abierto a un conductor disponible."""
    _require_crm_operator(user)
    try:
        driver_user_id = int(payload.get("driver_user_id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="driver_user_id inválido")

    cur.execute(
        """WITH current AS (
                 SELECT id, status AS previous_status
                   FROM livi_trips
                  WHERE id = %s
                  FOR UPDATE
             )
             UPDATE livi_trips t
                SET assigned_driver_user_id = %s,
                    tracking_driver_name = u.name,
                    status = 'assigned',
                    updated_at = NOW()
               FROM current
               JOIN livre_users u ON TRUE
               JOIN livre_driver_accounts a ON a.user_id = u.id
              WHERE t.id = current.id
                AND t.status IN ('searching', 'pending', 'assigned')
                AND t.assigned_driver_user_id IS NULL
                AND a.user_id = %s
                AND a.active = TRUE
                AND a.operational_status = 'available'
              RETURNING t.id, t.origen, t.destino, t.status,
                        t.assigned_driver_user_id, t.tracking_driver_name,
                        t.created_at, t.updated_at, current.previous_status""",
        (trip_id, driver_user_id, driver_user_id),
    )
    row = cur.fetchone()
    previous_status = row.get("previous_status") if row else None
    if not row:
        cur.execute("SELECT id, status, assigned_driver_user_id FROM livi_trips WHERE id = %s", (trip_id,))
        trip = cur.fetchone()
        if not trip:
            raise HTTPException(status_code=404, detail="Viaje no encontrado")
        if trip["assigned_driver_user_id"] is not None or trip["status"] not in {"searching", "pending", "assigned"}:
            raise HTTPException(status_code=409, detail="El viaje ya fue asignado o no está disponible")
        raise HTTPException(status_code=409, detail="El conductor no está disponible")
    record_trip_event(
        cur, trip_id=trip_id, actor_user_id=user["id"], actor_role="crm",
        event_type="trip_assigned", previous_status=previous_status, new_status="assigned",
        metadata={"driver_user_id": driver_user_id},
    )
    return {**dict(row), "source": "livre-cloud"}


@router.post("/trips/{trip_id}/tracking")
def update_trip_tracking(
    trip_id: int,
    payload: dict = Body(...),
    cur=Depends(get_cursor_dep),
    user: dict = Depends(_require_crm_operator),
):
    """Actualiza desde el CRM la ubicación y los datos públicos del chofer."""
    def text(key: str, max_length: int = 120) -> str | None:
        value = payload.get(key)
        if value is None:
            return None
        value = str(value).strip()
        return value[:max_length] or None

    latitude = payload.get("latitude")
    longitude = payload.get("longitude")
    if latitude is not None or longitude is not None:
        try:
            latitude = float(latitude)
            longitude = float(longitude)
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="Coordenadas inválidas")
        if not (math.isfinite(latitude) and math.isfinite(longitude)):
            raise HTTPException(status_code=422, detail="Coordenadas inválidas")
        if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
            raise HTTPException(status_code=422, detail="Coordenadas fuera de rango")

    driver_name = text("driver_first_name")
    plate = text("plate")
    vehicle_color = text("vehicle_color")
    vehicle_model = text("vehicle_model")
    cur.execute(
        """
        UPDATE livi_trips
        SET tracking_driver_name = COALESCE(NULLIF(%s, ''), tracking_driver_name),
            tracking_plate = COALESCE(NULLIF(%s, ''), tracking_plate),
            tracking_vehicle_color = COALESCE(NULLIF(%s, ''), tracking_vehicle_color),
            tracking_vehicle_model = COALESCE(NULLIF(%s, ''), tracking_vehicle_model),
            tracking_latitude = COALESCE(%s, tracking_latitude),
            tracking_longitude = COALESCE(%s, tracking_longitude),
            tracking_location_updated_at = CASE WHEN %s IS NULL THEN tracking_location_updated_at ELSE NOW() END,
            tracking_location_visible_at = CASE WHEN %s IS NULL THEN tracking_location_visible_at ELSE NOW() + INTERVAL '15 seconds' END,
            updated_at = NOW()
        WHERE id = %s
        RETURNING tracking_location_visible_at
        """,
        (driver_name, plate, vehicle_color, vehicle_model,
         latitude, longitude, latitude, latitude, trip_id),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Viaje no encontrado")
    return {"status": "updated", "trip_id": trip_id, "location_visible_at": row[0]}


def _operational_snapshot(cur, viewer_user_id: str | None = None):
    """Returns one consistent Livre snapshot of accounts, trips and GPS."""
    cur.execute(
        """
        SELECT a.user_id, a.active, a.operational_status,
               u.name, u.username,
               COALESCE(a.current_latitude, active_trip.tracking_latitude) AS latitude,
               COALESCE(a.current_longitude, active_trip.tracking_longitude) AS longitude,
               COALESCE(a.location_updated_at, active_trip.tracking_location_updated_at) AS location_updated_at,
               COALESCE(vehicle_link.vehicle_plate, vehicle_link.vehicle_label, active_trip.tracking_plate) AS associated_vehicle_plate,
               COALESCE(vehicle_link.vehicle_model, active_trip.tracking_vehicle_model) AS associated_vehicle_model,
               COALESCE(vehicle_link.vehicle_color, active_trip.tracking_vehicle_color) AS associated_vehicle_color,
               active_trip.id AS trip_id,
               active_trip.status AS trip_status,
               active_trip.tracking_driver_name,
               active_trip.tracking_plate,
               active_trip.tracking_vehicle_model,
               active_trip.tracking_vehicle_color
        FROM livre_driver_accounts a
        JOIN livre_users u ON u.id = a.user_id
        LEFT JOIN LATERAL (
            SELECT dv.vehicle_magiis_id,
                   dv.vehicle_label,
                   vehicle.plate AS vehicle_plate,
                   vehicle.model AS vehicle_model,
                   vehicle.color AS vehicle_color
              FROM livre_driver_vehicles dv
              LEFT JOIN livre_crm_vehicles vehicle
                ON vehicle.source_magiis_id = dv.vehicle_magiis_id
             WHERE dv.driver_user_id = a.user_id
             ORDER BY dv.is_primary DESC, dv.updated_at DESC NULLS LAST
             LIMIT 1
        ) vehicle_link ON TRUE
        LEFT JOIN LATERAL (
            SELECT t.id, t.status, t.tracking_driver_name, t.tracking_plate,
                   t.tracking_vehicle_model, t.tracking_vehicle_color,
                   t.tracking_latitude, t.tracking_longitude,
                   t.tracking_location_updated_at
            FROM livi_trips t
            WHERE t.assigned_driver_user_id = a.user_id
              AND t.status NOT IN ('completed', 'cancelled')
            ORDER BY t.updated_at DESC NULLS LAST, t.id DESC
            LIMIT 1
        ) active_trip ON TRUE
        WHERE a.active = TRUE
          AND (a.user_id <> ALL(%s) OR a.user_id = %s)
        ORDER BY u.name NULLS LAST, a.user_id
        """,
        (list(_HIDDEN_GPS_DRIVER_USER_IDS), viewer_user_id),
    )
    items = []
    for row in cur.fetchall():
        busy = row["trip_status"] in {"assigned", "arriving", "in_progress"}
        operational_status = row["operational_status"]
        operational_label = {
            "available": "Disponible",
            "unavailable": "No disponible",
            "out_of_service": "Fuera de servicio",
        }[operational_status]
        state = "EN VIAJE" if busy else operational_label
        driver_name = row["name"] or row["username"] or "Conductor"
        last_seen = row["location_updated_at"]
        if last_seen and last_seen.tzinfo is None:
            last_seen = last_seen.replace(tzinfo=timezone.utc)
        session_active = bool(last_seen and last_seen >= datetime.now(timezone.utc) - timedelta(minutes=2))
        items.append({
            "driverUserId": str(row["user_id"]),
            "driverName": driver_name,
            "sessionActive": session_active,
            "operationalStatus": operational_status,
            "operationalLabel": operational_label,
            "operationalState": state,
            "operability": state,
            "tripId": row["trip_id"],
            "tripStatus": row["trip_status"],
            "lastPlace": {
                "latitude": float(row["latitude"]),
                "longitude": float(row["longitude"]),
            } if row["latitude"] is not None and row["longitude"] is not None else None,
            "lastSee": row["location_updated_at"].isoformat() if row["location_updated_at"] else None,
            "vehicle": {
                "plate": row["associated_vehicle_plate"],
                "model": row["associated_vehicle_model"],
                "color": row["associated_vehicle_color"],
            },
            "source": "livre_driver_accounts+driver_vehicle_association+livi_trips",
        })
    return items


_OPERATIONAL_SCHEMA_TABLES = {
    "livi_trips",
    "livre_driver_accounts",
    "livre_driver_vehicles",
    "livre_users",
    "livi_whatsapp_quotes",
    "memory_documents",
    "memory_rows",
}


def _operational_schema(cur) -> dict:
    """Detecta capacidades sin DDL y sin ocultar errores de catálogo."""
    cur.execute(
        """
        SELECT table_name, column_name
          FROM information_schema.columns
         WHERE table_schema = 'public'
           AND table_name = ANY(%s)
        """,
        (sorted(_OPERATIONAL_SCHEMA_TABLES),),
    )
    rows = cur.fetchall()
    columns = {table: set() for table in _OPERATIONAL_SCHEMA_TABLES}
    for row in rows:
        table = row["table_name"]
        if table in columns:
            columns[table].add(row["column_name"])
    tables = {table for table, names in columns.items() if names}
    if "livi_trips" not in tables or "status" not in columns["livi_trips"]:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "CRM_SCHEMA_INCOMPATIBLE",
                "message": "Falta livi_trips.status; no se puede calcular la actividad operativa.",
            },
        )

    capabilities = {
        "trip_activity_type": "activity_type" in columns["livi_trips"],
        "trip_source": "source" in columns["livi_trips"],
        "local_driver_accounts": "livre_driver_accounts" in tables and "livre_users" in tables,
        "local_vehicle_links": "livre_driver_vehicles" in tables,
        "whatsapp_quotes": "livi_whatsapp_quotes" in tables,
        "conversation_transcripts": {
            "memory_documents": "memory_documents" in tables,
            "memory_rows": "memory_rows" in tables,
        },
    }
    warnings = []
    if not capabilities["trip_activity_type"]:
        warnings.append("livi_trips.activity_type no disponible: los viajes se agrupan como unclassified")
    if not capabilities["trip_source"]:
        warnings.append("livi_trips.source no disponible: WhatsApp usa el fallback whatsapp_user")
    if not capabilities["local_driver_accounts"]:
        warnings.append("livre_driver_accounts/livre_users incompletas: choferes no disponibles")
    if not capabilities["local_vehicle_links"]:
        warnings.append("livre_driver_vehicles no disponible: vehículos no disponibles")
    if not capabilities["whatsapp_quotes"]:
        warnings.append("livi_whatsapp_quotes no disponible: cotizaciones WhatsApp no disponibles")
    if not all(capabilities["conversation_transcripts"].values()):
        warnings.append("memory_documents/memory_rows incompletas: conversaciones con transcripción no disponibles")
    return {
        "status": "updated" if not warnings else "legacy",
        "capabilities": capabilities,
        "warnings": warnings,
    }


@router.get("/operational")
def operational(user: dict = Depends(_require_crm_operator), cur=Depends(get_cursor_dep)):
    """Returns one consistent, auditable Livre operational snapshot."""
    drivers = _operational_snapshot(cur, user.get("id"))
    schema = _operational_schema(cur)
    activity_type_sql = "COALESCE(activity_type, 'unclassified')" if schema["capabilities"]["trip_activity_type"] else "'unclassified'"
    cur.execute(
        f"""
        SELECT status, {activity_type_sql} AS activity_type, count(*) AS total
        FROM livi_trips
        GROUP BY status, {activity_type_sql}
        """
    )
    trip_totals = {(row["status"], row["activity_type"]): int(row["total"]) for row in cur.fetchall()}
    completed_real = trip_totals.get(("completed", "real"), 0)
    completed_test = trip_totals.get(("completed", "test"), 0)
    completed_unclassified = trip_totals.get(("completed", "unclassified"), 0)
    status_semantics = _trip_status_semantics(trip_totals)

    source_filter = (
        "source = 'whatsapp' OR (source IS NULL AND whatsapp_user IS NOT NULL)"
        if schema["capabilities"]["trip_source"]
        else "whatsapp_user IS NOT NULL"
    )
    # Keep missing optional metrics NULL: zero would falsely claim the source was checked.
    test_count = "(SELECT count(*) FROM livi_trips WHERE activity_type = 'test')" if schema["capabilities"]["trip_activity_type"] else "NULL::bigint"
    whatsapp_quotes_count = "(SELECT count(*) FROM livi_whatsapp_quotes)" if schema["capabilities"]["whatsapp_quotes"] else "NULL::bigint"
    transcripts_count = (
        "(SELECT count(DISTINCT md.key) FROM memory_documents md "
        "JOIN memory_rows mr ON mr.document_id = md.id "
        "WHERE md.key LIKE 'livi_%%')"
        if all(schema["capabilities"]["conversation_transcripts"].values())
        else "NULL::bigint"
    )
    cur.execute(
        f"""
        SELECT
          (SELECT count(*) FROM livi_trips) AS trips_total,
          (SELECT count(*) FROM livi_trips WHERE {source_filter}) AS whatsapp_trip_requests,
          {test_count} AS test_trips_total,
          NULL::bigint AS whatsapp_quote_requests,
          {whatsapp_quotes_count} AS whatsapp_quotes_total,
          {transcripts_count} AS whatsapp_conversations_with_transcript
        """
    )
    activity_row = cur.fetchone()
    metric = lambda name: None if activity_row[name] is None else int(activity_row[name])
    in_travel = sum(item["operationalState"] == "EN VIAJE" for item in drivers)
    available = sum(item["operationalStatus"] == "available" and item["operationalState"] != "EN VIAJE" for item in drivers)
    unavailable = sum(item["operationalStatus"] == "unavailable" and item["operationalState"] != "EN VIAJE" for item in drivers)
    out_of_service = sum(item["operationalStatus"] == "out_of_service" and item["operationalState"] != "EN VIAJE" for item in drivers)
    known_vehicles = {item["vehicle"]["plate"] for item in drivers if item["vehicle"].get("plate")}
    return {
        "source": "livre",
        "status": {
            "travelsQuantity": completed_real,
            "testTravelsQuantity": completed_test,
            "unclassifiedTravelsQuantity": completed_unclassified,
            "workingVehiclesQuantity": len(known_vehicles) if known_vehicles else None,
            "kilometersTotal": None,
            "clientsIndividualsAmount": None,
        },
        "activity": {
            "source": "livre-cloud",
            "schema": schema,
            "trips": {
                "total": metric("trips_total"),
                "by_status_and_type": {
                    f"{status}:{activity_type}": total
                    for (status, activity_type), total in trip_totals.items()
                },
                "test_total": metric("test_trips_total"),
                "status_semantics": status_semantics,
            },
            "whatsapp": {
                "trip_requests": metric("whatsapp_trip_requests"),
                "quote_requests": metric("whatsapp_quote_requests"),
                "quotes": metric("whatsapp_quotes_total"),
                "conversations_with_transcript": metric("whatsapp_conversations_with_transcript"),
            },
            "note": "Los contadores salen de tablas existentes; no se generan eventos paralelos.",
        },
        "trip_totals": {
            "completed_real": completed_real,
            "completed_test": completed_test,
            "completed_unclassified": completed_unclassified,
            "status_semantics": status_semantics,
        },
        "vehicle_totals": {
            "identified": len(known_vehicles),
            "unidentified": len(drivers) - len(known_vehicles),
        },
        "driver_totals": {
            "totalDrivers": len(drivers),
            "totalDriversOnline": available,
            "totalDriversOffline": unavailable + out_of_service,
            "totalDriversInTravel": in_travel,
            "available": available,
            "unavailable": unavailable,
            "out_of_service": out_of_service,
        },
        "drivers": drivers,
        "drivers_by_vehicle": drivers,
        "driver_statuses": {"items": drivers, "total": len(drivers)},
        "report_042": {},
        "chats": {},
    }


@router.get("/drivers/live")
def live_drivers(
    page: int = Query(default=1, ge=1, le=10000),
    size: int = Query(default=20, ge=1, le=100),
    driver_code: str = Query(default="", max_length=120),
    driver_name: str = Query(default="", max_length=120),
    phone: str = Query(default="", max_length=120),
    email: str = Query(default="", max_length=160),
    vehicle: str = Query(default="", max_length=120),
    vehicle_type: str = Query(default="", max_length=120),
    operability: str = Query(default="", max_length=120),
    user: dict = Depends(_require_crm_operator),
    cur=Depends(get_cursor_dep),
):
    """Listado de conductores desde livre_driver_accounts, sin snapshot externo."""
    return _local_driver_page(
        cur, page, size,
        driver_name or driver_code or phone or email,
        vehicle=vehicle,
        operability=operability,
    )


@router.get("/drivers/{driver_user_id}/detail")
def live_driver_detail(driver_user_id: str, user: dict = Depends(_require_crm_operator), cur=Depends(get_cursor_dep)):
    """Detalle del conductor según el registro propio de Livre."""
    cur.execute(
        """SELECT a.user_id, a.operational_status, a.active, u.name, u.username, u.email
             FROM livre_driver_accounts a JOIN livre_users u ON u.id = a.user_id
            WHERE a.user_id = %s""", (str(driver_user_id),)
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Conductor no encontrado en Livre")
    return {**dict(row), "source": "livre-cloud"}

@router.get("/operational/driver/{driver_user_id}")
def operational_driver(driver_user_id: str, user: dict = Depends(_require_crm_operator), cur=Depends(get_cursor_dep)):
    items = _operational_snapshot(cur, user.get("id"))
    return next((item for item in items if item["driverUserId"] == str(driver_user_id)), None) or (_raise_driver_not_found())


def _raise_driver_not_found():
    raise HTTPException(status_code=404, detail="Conductor no encontrado en Livre")


@router.put("/operational/driver/{driver_user_id}/status", response_model=DriverOperationalStatusResponse)
def update_operational_driver_status(
    driver_user_id: int,
    payload: DriverOperationalStatusUpdate,
    user: dict = Depends(_require_crm_operator),
    cur=Depends(get_cursor_dep),
):
    cur.execute(
        """UPDATE livre_driver_accounts
           SET operational_status = %s, updated_at = NOW()
           WHERE user_id = %s AND active = TRUE
           RETURNING operational_status""",
        (payload.status, driver_user_id),
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Conductor no encontrado o no habilitado")
    return {"status": row["operational_status"]}


@router.post("/sync")
def sync_crm(user: dict = Depends(_require_crm_operator)):
    raise HTTPException(
        status_code=410,
        detail="La sincronización MAGIIS fue retirada: Livre Cloud es la fuente operativa.",
    )


@router.get("/summary")
def summary(user: dict = Depends(_require_crm_operator), cur=Depends(get_cursor_dep)):
    cur.execute(
        """
        SELECT
          NULL::bigint AS passengers,
          (SELECT count(*) FROM livre_driver_accounts WHERE active = TRUE) AS drivers,
          (SELECT count(DISTINCT vehicle_label) FROM livre_driver_vehicles WHERE NULLIF(trim(vehicle_label), '') IS NOT NULL) AS vehicles,
          (SELECT count(*) FROM livi_trips) AS travels,
          (SELECT jsonb_object_agg(COALESCE(status, 'sin_estado'), total)
             FROM (SELECT status, count(*) AS total FROM livi_trips GROUP BY status) s) AS travel_states,
          NULL::json AS last_sync
        """
    )
    row = dict(cur.fetchone())
    return {k: int(v) if k in _RESOURCES and v is not None else v for k, v in row.items()}


@router.get("/travels/live")
def live_travels(
    page: int = Query(default=1, ge=1, le=10000),
    size: int = Query(default=20, ge=1, le=100),
    q: str = Query(default="", max_length=120),
    state: str = Query(default="", max_length=120),
    programmed: str = Query(default="", max_length=20),
    date_from: str = Query(default="", max_length=10),
    date_to: str = Query(default="", max_length=10),
    vip: bool = Query(default=False),
    user: dict = Depends(_require_crm_operator),
    cur=Depends(get_cursor_dep),
):
    """Viajes creados en Livre y pendientes/asignados desde el CRM."""
    return _local_trip_page(cur, page, size, q, state, date_from, date_to)


_PREMIUM_TRIPS_SQL = """
    SELECT t.id AS trip_id,
           COALESCE(t.original_fare, t.fare)::numeric(12,2) AS total,
           COALESCE(t.tracking_plate, dv.vehicle_label) AS vehicle,
           COALESCE(t.tracking_vehicle_model, v.model) AS vehicle_model,
           NULLIF(trim(concat(o.first_name, ' ', o.last_name)), '') AS owner,
           t.updated_at::date AS completed_at,
           c.id AS commission_id,
           c.settled_at
      FROM livi_trips t
      LEFT JOIN livre_driver_vehicles dv
        ON dv.driver_user_id = t.assigned_driver_user_id
       AND (t.tracking_plate IS NULL OR upper(dv.vehicle_label) = upper(t.tracking_plate))
      LEFT JOIN livre_crm_vehicles v
        ON upper(v.plate) = upper(COALESCE(t.tracking_plate, dv.vehicle_label))
      LEFT JOIN livre_crm_vehicle_owners o ON o.id = v.owner_id
      LEFT JOIN LATERAL (
          SELECT id, settled_at
            FROM livre_crm_commissions
           WHERE trip_id = t.id
           ORDER BY id
           LIMIT 1
      ) c ON TRUE
     WHERE t.status = 'completed'
       AND t.service_type_id = '1'
"""


def _agency_filters(date_from: str, date_to: str, owner_id: str):
    filters, params = [], []
    if date_from:
        filters.append("completed_at >= %s"); params.append(date_from)
    if date_to:
        filters.append("completed_at <= %s"); params.append(date_to)
    if owner_id:
        filters.append("owner = %s"); params.append(owner_id)
    return (" WHERE " + " AND ".join(filters)) if filters else "", params


@router.get("/agency-commissions")
def agency_commissions(
    date_from: str = Query(default="", max_length=10),
    date_to: str = Query(default="", max_length=10),
    owner_id: str = Query(default="", max_length=120),
    page: int = Query(default=1, ge=1, le=10000),
    size: int = Query(default=20, ge=1, le=100),
    user: dict = Depends(_require_crm_operator),
    cur=Depends(get_cursor_dep),
):
    """Calcula la comisión real sobre viajes Premium completados."""
    where, params = _agency_filters(date_from, date_to, owner_id)
    cur.execute(f"SELECT count(*) AS total, count(DISTINCT vehicle) AS vehicles, COALESCE(sum(total), 0) AS total_amount FROM ({_PREMIUM_TRIPS_SQL}) premium{where}", params)
    totals = cur.fetchone()
    cur.execute(
        f"SELECT trip_id AS travel_id, vehicle, vehicle_model, owner, total, ROUND(total * 0.15, 2) AS commission, 15.00 AS commission_percent, completed_at, commission_id IS NOT NULL AS settled, settled_at FROM ({_PREMIUM_TRIPS_SQL}) premium{where} ORDER BY completed_at DESC, trip_id DESC LIMIT %s OFFSET %s",
        params + [size, (page - 1) * size],
    )
    items = [dict(row) for row in cur.fetchall()]
    cur.execute(f"SELECT DISTINCT owner FROM ({_PREMIUM_TRIPS_SQL}) premium WHERE owner IS NOT NULL ORDER BY owner")
    owners = [{"id": row["owner"], "name": row["owner"]} for row in cur.fetchall()]
    total = int(totals["total"])
    total_amount = float(totals["total_amount"])
    return {"source": "livre-cloud", "owners": owners, "items": items, "total": total, "page": page, "size": size,
            "totals": {"trips": total, "vehicles": int(totals["vehicles"] or 0), "total": total_amount, "commission": round(total_amount * 0.15, 2), "commission_percent": 15.0},
            "warning": None if total else "No hay viajes Premium completados para liquidar."}


@router.post("/agency-commissions/settle")
def settle_agency_commissions(user: dict = Depends(_require_crm_operator), cur=Depends(get_cursor_dep)):
    """Crea una liquidación idempotente por cada viaje Premium completado pendiente."""
    cur.execute("SELECT pg_advisory_xact_lock(987654321)")
    cur.execute(
        f"""INSERT INTO livre_crm_commissions
                (trip_id, vehicle, vehicle_model, owner, total, commission, commission_percent, settled_at, raw_payload)
            SELECT p.trip_id, p.vehicle, p.vehicle_model, p.owner, p.total,
                   ROUND(p.total * 0.15, 2), 15.00, NOW(),
                   jsonb_build_object('source', 'completed_premium_trip', 'service_type_id', '1')
              FROM ({_PREMIUM_TRIPS_SQL}) p
             WHERE p.commission_id IS NULL
               AND NOT EXISTS (SELECT 1 FROM livre_crm_commissions existing WHERE existing.trip_id = p.trip_id)"""
    )
    settled = cur.rowcount
    return {"source": "livre-cloud", "settled_count": settled, "message": "Liquidación Premium actualizada."}


@router.get("/driver-shift-closures")
def driver_shift_closures(
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    driver_id: str = Query(default="", max_length=80),
    page: int = Query(default=1, ge=1, le=10000),
    size: int = Query(default=50, ge=1, le=100),
    user: dict = Depends(_require_crm_operator),
    cur=Depends(get_cursor_dep),
):
    """Cierres de turno registrados por los conductores en Livre Cloud."""
    if date_from and date_to and date_to < date_from:
        raise HTTPException(status_code=422, detail="date_to no puede ser anterior a date_from")
    filters, params = [], []
    if date_from:
        filters.append("c.closure_date >= %s"); params.append(date_from)
    if date_to:
        filters.append("c.closure_date <= %s"); params.append(date_to)
    if driver_id.strip():
        try:
            params.append(uuid.UUID(driver_id.strip()))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="driver_id inválido") from exc
        params[-1] = str(params[-1])
        filters.append("c.driver_user_id = %s")
    where = " WHERE " + " AND ".join(filters) if filters else ""
    cur.execute(f"SELECT count(*) AS total FROM livre_driver_shift_closures c{where}", params)
    total = int(cur.fetchone()["total"])
    cur.execute(
        f"""SELECT c.id, c.closure_date, c.trip_count, c.total_revenue,
                   c.driver_revenue, c.commission, c.created_at,
                   c.driver_user_id, COALESCE(u.name, u.username, c.driver_user_id::text) AS driver_name,
                   COALESCE(v.vehicle_labels, ARRAY[]::text[]) AS vehicle_labels
              FROM livre_driver_shift_closures c
              LEFT JOIN livre_users u ON u.id = c.driver_user_id
              LEFT JOIN LATERAL (
                  SELECT ARRAY_AGG(DISTINCT dv.vehicle_label ORDER BY dv.vehicle_label)
                    FILTER (WHERE NULLIF(trim(dv.vehicle_label), '') IS NOT NULL) AS vehicle_labels
                    FROM livre_driver_vehicles dv
                   WHERE dv.driver_user_id = c.driver_user_id
              ) v ON TRUE
              {where}
             ORDER BY c.closure_date DESC, c.created_at DESC, c.id DESC
             LIMIT %s OFFSET %s""",
        params + [size, (page - 1) * size],
    )
    items = [dict(row) for row in cur.fetchall()]
    return {"source": "livre-cloud", "items": items, "total": total, "page": page, "size": size}


@router.get("/conversations")
def conversations(
    q: str = Query(default="", max_length=120),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(_require_crm_operator),
    cur=Depends(get_cursor_dep),
):
    """Conversaciones/transcripciones locales de Livre, sin fuente externa."""
    term = f"%{q.strip()}%"
    params = [term, term] if q.strip() else []
    where = " WHERE (md.key ILIKE %s OR COALESCE(md.name, '') ILIKE %s)" if params else ""
    cur.execute(f"SELECT count(*) AS total FROM memory_documents md{where}", params)
    total = int(cur.fetchone()["total"])
    cur.execute(f"""SELECT md.id, md.key, md.name, md.description, md.chat_id, md.created_at,
        count(mr.id) AS rows_count, max(mr.created_at) AS updated_at
        FROM memory_documents md LEFT JOIN memory_rows mr ON mr.document_id = md.id{where}
        GROUP BY md.id ORDER BY max(mr.created_at) DESC NULLS LAST, md.created_at DESC LIMIT %s OFFSET %s""", params + [limit, offset])
    items = [dict(row) for row in cur.fetchall()]
    return {"source": "livre-cloud", "items": items, "total": total, "limit": limit, "offset": offset, "warning": None if total else "No hay conversaciones locales cargadas."}


@router.get("/travel-history")
def travel_history(
    page: int = Query(default=1, ge=1, le=10000),
    size: int = Query(default=50, ge=1, le=100),
    q: str = Query(default="", max_length=120),
    state: str = Query(default="", max_length=120),
    date_from: str = Query(default="", max_length=10),
    date_to: str = Query(default="", max_length=10),
    user: dict = Depends(_require_crm_operator),
    cur=Depends(get_cursor_dep),
):
    """Histórico de viajes de livi_trips; no agrega reportes externos."""
    result = _local_trip_page(cur, page, size, q, state, date_from, date_to)
    result["summary"] = {"trips": result["total"], "final_amount_ars": None, "amount_ars": None, "budget_ars": None}
    result["warning"] = "Los importes no tienen una fuente confiable en Livre y se muestran como —."
    return result


@router.get("/travel-history/{travel_id}/route")
def travel_route(
    travel_id: int,
    user: dict = Depends(_require_crm_operator),
    cur=Depends(get_cursor_dep),
):
    """Returns the persisted route only for completed trips and livreuser."""
    _require_livreuser(cur, user)
    cur.execute(
        """
        SELECT id, origen, destino, status, assigned_driver_user_id
        FROM livi_trips
        WHERE id = %s AND status = 'completed'
        """,
        (travel_id,),
    )
    trip = cur.fetchone()
    if not trip:
        raise HTTPException(status_code=404, detail="Recorrido no disponible: el viaje no está terminado")
    cur.execute(
        """
        SELECT latitude, longitude, recorded_at
        FROM livre_trip_location_history
        WHERE trip_id = %s
        ORDER BY recorded_at ASC, id ASC
        """,
        (travel_id,),
    )
    points = [dict(row) for row in cur.fetchall()]
    return {
        "source": "livre-cloud",
        "trip_id": trip["id"],
        "origin": trip["origen"],
        "destination": trip["destino"],
        "status": trip["status"],
        "points": points,
        "total": len(points),
    }


@router.get("/passengers")
def live_passengers(
    page: int = Query(default=1, ge=1, le=10000),
    size: int = Query(default=20, ge=1, le=100),
    q: str = Query(default="", max_length=120),
    user: dict = Depends(_require_crm_operator),
    cur=Depends(get_cursor_dep),
):
    """Clientes almacenados en la base local."""
    return _local_page(cur, "livre_crm_passengers", page, size, q)


@router.get("/passengers/{passenger_id}")
def live_passenger(passenger_id: int, user: dict = Depends(_require_crm_operator), cur=Depends(get_cursor_dep)):
    cur.execute("SELECT id, first_name, last_name, email, phone, country, raw_payload FROM livre_crm_passengers WHERE id = %s", (passenger_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Cliente no encontrado en Livre Cloud")
    return {"id": row["id"], "firstName": row["first_name"], "lastName": row["last_name"], "email": row["email"], "phone": row["phone"], "country": row["country"], **(row["raw_payload"] or {}), "source": "livre-cloud"}


@router.put("/travels/{travel_id}/cancel")
def cancel_live_travel(
    travel_id: int,
    reason: str = Query(default="Cancelación solicitada desde Gestión de Viajes", max_length=250),
    user: dict = Depends(_require_crm_operator),
    cur=Depends(get_cursor_dep),
):
    """Cancela el registro local de Livre, sin proveedor externo."""
    cur.execute(
        """UPDATE livi_trips SET status = 'cancelled', updated_at = NOW()
            WHERE id = %s AND status NOT IN ('completed', 'cancelled')
            RETURNING id""", (travel_id,)
    )
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Viaje no encontrado o ya finalizado")
    cur.execute(
        "DELETE FROM livre_trip_location_history WHERE trip_id = %s",
        (travel_id,),
    )
    return {"travel_id": travel_id, "status": "cancelled", "message": "Viaje cancelado correctamente.", "source": "livre-cloud"}


@router.get("/vehicles/live")
def live_vehicles(page: int = Query(default=1, ge=1), size: int = Query(default=20, ge=1, le=100), q: str = Query(default="", max_length=120), user: dict = Depends(_require_crm_operator), cur=Depends(get_cursor_dep)):
    filters, params = [], []
    if q.strip():
        filters.append("(plate ILIKE %s OR brand ILIKE %s OR model ILIKE %s)")
        params = [f"%{q.strip()}%"] * 3
    where = " WHERE " + " AND ".join(filters) if filters else ""
    cur.execute(f"SELECT count(*) AS total FROM livre_crm_vehicles{where}", params)
    total = int(cur.fetchone()["total"])
    cur.execute(f"SELECT * FROM livre_crm_vehicles{where} ORDER BY id DESC LIMIT %s OFFSET %s", params + [size, (page - 1) * size])
    return {"source": "livre-cloud", "items": [dict(row) for row in cur.fetchall()], "page": page, "size": size, "total": total}



@router.get("/vehicles/types")
def live_vehicle_types(user: dict = Depends(_require_crm_operator)):
    return {"items": [{"id": "taxi", "name": "Taxi"}, {"id": "remis", "name": "Remis"}, {"id": "van", "name": "Van"}], "source": "livre-cloud"}


@router.post("/vehicles", status_code=201)
def create_live_vehicle(payload: dict = Body(...), user: dict = Depends(_require_crm_operator), cur=Depends(get_cursor_dep)):
    card = payload.get("identificationCard") or payload
    characteristics = payload.get("characteristics") or {}
    normalized = {
        "plate": card.get("plate") or card.get("domain"), "brand": card.get("brand") or card.get("mark"),
        "model": card.get("model"), "year": card.get("year") or card.get("yearOfVehicle"),
        "color": card.get("color"), "chassis": card.get("chassis"), "motor": card.get("motor"),
        "vehicleType": card.get("vehicleType") or card.get("vehicleTypeId") or card.get("type"),
        "ownerId": payload.get("ownerId") or payload.get("vehicleOwnerId"),
        "imageUrl": payload.get("imageUrl") or payload.get("photo"),
    }
    required = ("plate", "brand", "model", "year", "color", "vehicleType")
    if any(not str(normalized.get(key) or "").strip() for key in required):
        raise HTTPException(status_code=422, detail="Patente, marca, modelo, año, color y tipo son obligatorios")
    cur.execute("""INSERT INTO livre_crm_vehicles (plate, brand, model, year, color, chassis, motor, vehicle_type, owner_id, image_url, raw_payload)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb) RETURNING *""",
                (str(normalized["plate"]).strip().upper(), str(normalized["brand"]).strip(), str(normalized["model"]).strip(), int(normalized["year"]), str(normalized["color"]).strip(), normalized["chassis"], normalized["motor"], normalized["vehicleType"], normalized["ownerId"], normalized["imageUrl"], json.dumps(payload)))
    return {**dict(cur.fetchone()), "source": "livre-cloud"}


@router.post("/drivers", status_code=201)
def create_live_driver(payload: dict = Body(...), user: dict = Depends(_require_crm_operator), cur=Depends(get_cursor_dep)):
    first_name, last_name, email = str(payload.get("firstName", "")).strip(), str(payload.get("lastName", "")).strip(), str(payload.get("email", "")).strip().lower()
    if not first_name or not last_name or "@" not in email:
        raise HTTPException(status_code=422, detail="Nombre, apellido y email válido son obligatorios")
    driver_id = str(uuid.uuid4())
    password_hash = bcrypt.hashpw(secrets.token_urlsafe(24).encode(), bcrypt.gensalt()).decode()
    cur.execute("INSERT INTO livre_users (id, username, email, name, password_hash) VALUES (%s,%s,%s,%s,%s) RETURNING id, username, email, name", (driver_id, email, email, f"{first_name} {last_name}", password_hash))
    created = dict(cur.fetchone())
    cur.execute("INSERT INTO livre_driver_accounts (user_id, active) VALUES (%s, TRUE)", (driver_id,))
    return {**created, "source": "livre-cloud", "operational_status": "unavailable"}



@router.post("/clients", status_code=201)
def create_live_client(payload: dict = Body(...), user: dict = Depends(_require_crm_operator), cur=Depends(get_cursor_dep)):
    first_name = str(payload.get("firstName", "")).strip()
    last_name = str(payload.get("lastName", "")).strip()
    phone = str(payload.get("phone", "")).strip()
    if not first_name or not last_name or not phone:
        raise HTTPException(status_code=422, detail="Nombre, apellido y teléfono son obligatorios")
    cur.execute("""INSERT INTO livre_crm_passengers (first_name, last_name, email, phone, country, raw_payload)
                   VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                   RETURNING id, first_name, last_name, email, phone, country""",
                (first_name, last_name, payload.get("email"), phone, payload.get("country") or "Argentina", json.dumps(payload)))
    row = dict(cur.fetchone())
    return {"id": row["id"], "firstName": row["first_name"], "lastName": row["last_name"], "email": row["email"], "phone": row["phone"], "country": row["country"], "source": "livre-cloud"}


@router.get("/vehicle-owners")
def live_vehicle_owners(page: int = Query(default=1, ge=1), size: int = Query(default=50, ge=1, le=100), q: str = Query(default="", max_length=120), user: dict = Depends(_require_crm_operator), cur=Depends(get_cursor_dep)):
    return _local_page(cur, "livre_crm_vehicle_owners", page, size, q)


@router.post("/confirm-email")
def confirm_crm_email(payload: dict = Body(...), user: dict = Depends(_require_crm_operator)):
    email = str(payload.get("email", "")).strip()
    role = str(payload.get("role", "")).strip().upper()
    if "@" not in email or role not in {"DRIVER", "VEHICLEOWNER"}:
        raise HTTPException(status_code=422, detail="Email y rol son obligatorios")
    return {"email": email, "role": role, "verified": False, "source": "livre-cloud", "message": "El email quedó validado por formato; Livre no usa verificación externa."}


@router.get("/vehicle-owners/{owner_id}")
def live_vehicle_owner(owner_id: int, user: dict = Depends(_require_crm_operator), cur=Depends(get_cursor_dep)):
    cur.execute("SELECT * FROM livre_crm_vehicle_owners WHERE id = %s", (owner_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Propietario no encontrado en Livre Cloud")
    return {**dict(row), "source": "livre-cloud"}


@router.post("/vehicle-owners", status_code=201)
def create_live_vehicle_owner(payload: dict = Body(...), user: dict = Depends(_require_crm_operator), cur=Depends(get_cursor_dep)):
    first_name, last_name = str(payload.get("firstName", "")).strip(), str(payload.get("lastName", "")).strip()
    if not first_name or not last_name:
        raise HTTPException(status_code=422, detail="Nombre y apellido son obligatorios")
    cur.execute("""INSERT INTO livre_crm_vehicle_owners (first_name, last_name, identification, email, phone, city, province, street, street_number, notes, raw_payload)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb) RETURNING *""",
                (first_name, last_name, payload.get("cuitNumberTitular") or payload.get("idNumber"), payload.get("contactEmail"), payload.get("contactPhone"), payload.get("city"), payload.get("province"), payload.get("streetName"), payload.get("streetNumber"), payload.get("notes"), json.dumps(payload)))
    return {**dict(cur.fetchone()), "source": "livre-cloud"}


@router.put("/vehicle-owners/{owner_id}")
def update_live_vehicle_owner(owner_id: int, payload: dict = Body(...), user: dict = Depends(_require_crm_operator), cur=Depends(get_cursor_dep)):
    cur.execute("""UPDATE livre_crm_vehicle_owners SET first_name=%s,last_name=%s,identification=%s,email=%s,phone=%s,city=%s,province=%s,street=%s,street_number=%s,notes=%s,raw_payload=%s::jsonb,updated_at=now() WHERE id=%s RETURNING *""",
                (payload.get("firstName", "").strip(), payload.get("lastName", "").strip(), payload.get("cuitNumberTitular") or payload.get("idNumber"), payload.get("contactEmail"), payload.get("contactPhone"), payload.get("city"), payload.get("province"), payload.get("streetName"), payload.get("streetNumber"), payload.get("notes"), json.dumps(payload), owner_id))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Propietario no encontrado en Livre Cloud")
    return {**dict(row), "source": "livre-cloud"}


@router.put("/vehicles/{vehicle_id}")
def update_live_vehicle(vehicle_id: int, payload: dict = Body(...), user: dict = Depends(_require_crm_operator), cur=Depends(get_cursor_dep)):
    card = payload.get("identificationCard") or payload
    normalized = {"plate": card.get("plate") or card.get("domain"), "brand": card.get("brand") or card.get("mark"), "model": card.get("model"), "year": card.get("year") or card.get("yearOfVehicle"), "color": card.get("color"), "chassis": card.get("chassis"), "motor": card.get("motor"), "vehicleType": card.get("vehicleType") or card.get("vehicleTypeId") or card.get("type"), "ownerId": payload.get("ownerId") or payload.get("vehicleOwnerId"), "imageUrl": payload.get("imageUrl") or payload.get("photo")}
    if any(not str(normalized.get(key) or "").strip() for key in ("plate", "brand", "model", "year", "color", "vehicleType")):
        raise HTTPException(status_code=422, detail="Patente, marca, modelo, año, color y tipo son obligatorios")
    cur.execute("""UPDATE livre_crm_vehicles SET plate=%s, brand=%s, model=%s, year=%s, color=%s, chassis=%s, motor=%s, vehicle_type=%s, owner_id=%s, image_url=%s, raw_payload=%s::jsonb, updated_at=now() WHERE id=%s RETURNING *""",
                (str(normalized["plate"]).strip().upper(), str(normalized["brand"]).strip(), str(normalized["model"]).strip(), int(normalized["year"]), str(normalized["color"]).strip(), normalized["chassis"], normalized["motor"], normalized["vehicleType"], normalized["ownerId"], normalized["imageUrl"], json.dumps(payload), vehicle_id))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado en Livre Cloud")
    return {**dict(row), "source": "livre-cloud"}


@router.get("/{resource}")
def resource(
    resource: str,
    q: str = Query(default="", max_length=120),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    state: str = Query(default="", max_length=120),
    travel_date: str = Query(default="", max_length=10),
    user: dict = Depends(_require_crm_operator),
    cur=Depends(get_cursor_dep),
):
    if resource == "drivers":
        page = offset // limit + 1
        result = _local_driver_page(cur, page, limit, q)
        return {"resource": resource, "total": result["total"], "limit": limit, "offset": offset, "items": result["items"]}
    table = _RESOURCES.get(resource)
    if not table:
        raise HTTPException(status_code=404, detail="Recurso CRM inexistente")
    if resource == "travels":
        result = _local_trip_page(cur, offset // limit + 1, limit, q, state, "", "")
        return {"resource": resource, "total": result["total"], "limit": limit, "offset": offset, "items": result["items"], "totals": result.get("totals")}
    result = _local_page(cur, table, offset // limit + 1, limit, q)
    return {"resource": resource, "total": result["total"], "limit": limit, "offset": offset, "items": result["items"]}
