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


def _local_driver_page(cur, page: int, size: int, q: str = ""):
    """Lista conductores desde la identidad y estado propios de Livre."""
    filters = ["a.active = TRUE"]
    params = []
    if q.strip():
        term = "%" + q.strip() + "%"
        filters.append("(u.name ILIKE %s OR u.username ILIKE %s OR u.email ILIKE %s OR a.user_id::text ILIKE %s)")
        params.extend([term, term, term, term])
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
        if requested & {"SEARCHING_DRIVER", "SCHEDULED"}:
            local_states.add("pending")
        if requested & {"WITH_DRIVER_ASSIGNED", "RESERVED", "GOING_TO_CLIENT", "GOING_TO_DESTINATION", "CLOSING_TRAVEL", "IN_CONFLICT"}:
            local_states.add("confirmed")
        if requested & {"CANCELED", "CANCELLED"}:
            local_states.add("cancelled")
        if requested & {"LOST"}:
            local_states.add("lost")
        if requested & {"DONE"}:
            local_states.add("done")
        if local_states:
            placeholders = ", ".join(["%s"] * len(local_states))
            filters.append(f"status IN ({placeholders})")
            params.extend(sorted(local_states))
    if date_from:
        filters.append("created_at >= %s::date")
        params.append(date_from)
    if date_to:
        filters.append("created_at < (%s::date + INTERVAL '1 day')")
        params.append(date_to)
    summary_filters = []
    summary_params = []
    if q.strip():
        summary_filters.append("(origen ILIKE %s OR destino ILIKE %s OR whatsapp_user ILIKE %s)")
        summary_params.extend(["%" + q.strip() + "%"] * 3)
    if date_from:
        summary_filters.append("created_at >= %s::date")
        summary_params.append(date_from)
    if date_to:
        summary_filters.append("created_at < (%s::date + INTERVAL '1 day')")
        summary_params.append(date_to)
    summary_where = " WHERE " + " AND ".join(summary_filters) if summary_filters else ""
    cur.execute(
        f"SELECT status, count(*) AS total FROM livi_trips{summary_where} GROUP BY status",
        summary_params,
    )
    status_totals = {row["status"]: int(row["total"]) for row in cur.fetchall()}
    totals = {
        "toAsignQuantity": status_totals.get("pending", 0),
        "inProgressQuantity": status_totals.get("confirmed", 0),
        "scheduledQuantity": 0,
        "inConflictQuantity": status_totals.get("conflict", 0),
        "lostQuantity": status_totals.get("lost", 0),
        "cancelledQuantity": status_totals.get("cancelled", 0),
        "doneQuantity": status_totals.get("done", 0),
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
    return {"source": "livre", "items": items, "page": page, "size": size, "total": total, "totals": totals}


@router.post("/trips/{trip_id}/tracking")
def update_trip_tracking(
    trip_id: int,
    payload: dict = Body(...),
    cur=Depends(get_cursor_dep),
    user: dict = Depends(require_auth),
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
               COALESCE(vehicle_link.vehicle_label, active_trip.tracking_plate) AS associated_vehicle_plate,
               NULL::text AS associated_vehicle_model,
               NULL::text AS associated_vehicle_color,
               active_trip.id AS trip_id,
               active_trip.status AS trip_status,
               active_trip.tracking_driver_name,
               active_trip.tracking_plate,
               active_trip.tracking_vehicle_model,
               active_trip.tracking_vehicle_color
        FROM livre_driver_accounts a
        JOIN livre_users u ON u.id = a.user_id
        LEFT JOIN LATERAL (
            SELECT vehicle_label
              FROM livre_driver_vehicles
             WHERE driver_user_id = a.user_id
             ORDER BY updated_at DESC NULLS LAST
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
def operational(user: dict = Depends(require_auth), cur=Depends(get_cursor_dep)):
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
    user: dict = Depends(require_auth),
    cur=Depends(get_cursor_dep),
):
    """Listado de conductores desde livre_driver_accounts, sin snapshot externo."""
    return _local_driver_page(cur, page, size, driver_name or driver_code or phone or email or vehicle)


@router.get("/drivers/{driver_user_id}/detail")
def live_driver_detail(driver_user_id: str, user: dict = Depends(require_auth), cur=Depends(get_cursor_dep)):
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
def operational_driver(driver_user_id: str, user: dict = Depends(require_auth), cur=Depends(get_cursor_dep)):
    items = _operational_snapshot(cur, user.get("id"))
    return next((item for item in items if item["driverUserId"] == str(driver_user_id)), None) or (_raise_driver_not_found())


def _raise_driver_not_found():
    raise HTTPException(status_code=404, detail="Conductor no encontrado en Livre")


@router.put("/operational/driver/{driver_user_id}/status", response_model=DriverOperationalStatusResponse)
def update_operational_driver_status(
    driver_user_id: int,
    payload: DriverOperationalStatusUpdate,
    user: dict = Depends(require_auth),
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
def sync_crm(user: dict = Depends(require_auth)):
    raise HTTPException(
        status_code=410,
        detail="La sincronización MAGIIS fue retirada: Livre Cloud es la fuente operativa.",
    )


@router.get("/summary")
def summary(user: dict = Depends(require_auth), cur=Depends(get_cursor_dep)):
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
    user: dict = Depends(require_auth),
    cur=Depends(get_cursor_dep),
):
    """Viajes creados en Livre y pendientes/asignados desde el CRM."""
    return _local_trip_page(cur, page, size, q, state, date_from, date_to)


@router.get("/agency-commissions")
def agency_commissions(
    date_from: str = Query(default="", max_length=10),
    date_to: str = Query(default="", max_length=10),
    owner_id: str = Query(default="", max_length=30),
    page: int = Query(default=1, ge=1, le=10000),
    size: int = Query(default=20, ge=1, le=100),
    user: dict = Depends(require_auth),
    cur=Depends(get_cursor_dep),
):
    """Liquidaciones almacenadas por Livre Cloud; no consulta proveedores."""
    filters, params = [], []
    if date_from:
        filters.append("settled_at::date >= %s"); params.append(date_from)
    if date_to:
        filters.append("settled_at::date <= %s"); params.append(date_to)
    if owner_id:
        filters.append("owner = %s"); params.append(owner_id)
    where = (" WHERE " + " AND ".join(filters)) if filters else ""
    cur.execute(f"SELECT count(*) AS total, COALESCE(sum(total), 0) AS total_amount, COALESCE(sum(commission), 0) AS commission_amount FROM livre_crm_commissions{where}", params)
    totals = cur.fetchone()
    cur.execute(f"SELECT id, trip_id AS travel_id, vehicle, vehicle_model, owner, total, commission, commission_percent, settled_at FROM livre_crm_commissions{where} ORDER BY settled_at DESC NULLS LAST, id DESC LIMIT %s OFFSET %s", params + [size, (page - 1) * size])
    items = [dict(row) for row in cur.fetchall()]
    cur.execute("SELECT DISTINCT owner FROM livre_crm_commissions WHERE owner IS NOT NULL AND owner <> '' ORDER BY owner")
    owners = [{"id": row["owner"], "name": row["owner"]} for row in cur.fetchall()]
    total = int(totals["total"])
    return {"source": "livre-cloud", "owners": owners, "items": items, "total": total, "page": page, "size": size, "totals": {"trips": total, "vehicles": len({item["vehicle"] for item in items if item["vehicle"]}), "total": float(totals["total_amount"]), "commission": float(totals["commission_amount"]), "commission_percent": None}, "warning": None if total else "No hay liquidaciones locales cargadas en Livre Cloud."}


@router.get("/conversations")
def conversations(
    q: str = Query(default="", max_length=120),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(require_auth),
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
    user: dict = Depends(require_auth),
    cur=Depends(get_cursor_dep),
):
    """Histórico de viajes de livi_trips; no agrega reportes externos."""
    result = _local_trip_page(cur, page, size, q, state, date_from, date_to)
    result["summary"] = {"trips": result["total"], "final_amount_ars": None, "amount_ars": None, "budget_ars": None}
    result["warning"] = "Los importes no tienen una fuente confiable en Livre y se muestran como —."
    return result


@router.get("/passengers")
def live_passengers(
    page: int = Query(default=1, ge=1, le=10000),
    size: int = Query(default=20, ge=1, le=100),
    q: str = Query(default="", max_length=120),
    user: dict = Depends(require_auth),
    cur=Depends(get_cursor_dep),
):
    """Clientes almacenados en la base local."""
    return _local_page(cur, "livre_crm_passengers", page, size, q)


@router.get("/passengers/{passenger_id}")
def live_passenger(passenger_id: int, user: dict = Depends(require_auth), cur=Depends(get_cursor_dep)):
    cur.execute("SELECT id, first_name, last_name, email, phone, country, raw_payload FROM livre_crm_passengers WHERE id = %s", (passenger_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Cliente no encontrado en Livre Cloud")
    return {"id": row["id"], "firstName": row["first_name"], "lastName": row["last_name"], "email": row["email"], "phone": row["phone"], "country": row["country"], **(row["raw_payload"] or {}), "source": "livre-cloud"}


@router.put("/travels/{travel_id}/cancel")
def cancel_live_travel(
    travel_id: int,
    reason: str = Query(default="Cancelación solicitada desde Gestión de Viajes", max_length=250),
    user: dict = Depends(require_auth),
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
    return {"travel_id": travel_id, "status": "cancelled", "message": "Viaje cancelado correctamente.", "source": "livre-cloud"}


@router.get("/vehicles/live")
def live_vehicles(page: int = Query(default=1, ge=1), size: int = Query(default=20, ge=1, le=100), q: str = Query(default="", max_length=120), user: dict = Depends(require_auth), cur=Depends(get_cursor_dep)):
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
def live_vehicle_types(user: dict = Depends(require_auth)):
    return {"items": [{"id": "taxi", "name": "Taxi"}, {"id": "remis", "name": "Remis"}, {"id": "van", "name": "Van"}], "source": "livre-cloud"}


@router.post("/vehicles", status_code=201)
def create_live_vehicle(payload: dict = Body(...), user: dict = Depends(require_auth), cur=Depends(get_cursor_dep)):
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
def create_live_driver(payload: dict = Body(...), user: dict = Depends(require_auth), cur=Depends(get_cursor_dep)):
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
def create_live_client(payload: dict = Body(...), user: dict = Depends(require_auth), cur=Depends(get_cursor_dep)):
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
def live_vehicle_owners(page: int = Query(default=1, ge=1), size: int = Query(default=50, ge=1, le=100), q: str = Query(default="", max_length=120), user: dict = Depends(require_auth), cur=Depends(get_cursor_dep)):
    return _local_page(cur, "livre_crm_vehicle_owners", page, size, q)


@router.post("/confirm-email")
def confirm_crm_email(payload: dict = Body(...), user: dict = Depends(require_auth)):
    email = str(payload.get("email", "")).strip()
    role = str(payload.get("role", "")).strip().upper()
    if "@" not in email or role not in {"DRIVER", "VEHICLEOWNER"}:
        raise HTTPException(status_code=422, detail="Email y rol son obligatorios")
    return {"email": email, "role": role, "verified": False, "source": "livre-cloud", "message": "El email quedó validado por formato; Livre no usa verificación externa."}


@router.get("/vehicle-owners/{owner_id}")
def live_vehicle_owner(owner_id: int, user: dict = Depends(require_auth), cur=Depends(get_cursor_dep)):
    cur.execute("SELECT * FROM livre_crm_vehicle_owners WHERE id = %s", (owner_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Propietario no encontrado en Livre Cloud")
    return {**dict(row), "source": "livre-cloud"}


@router.post("/vehicle-owners", status_code=201)
def create_live_vehicle_owner(payload: dict = Body(...), user: dict = Depends(require_auth), cur=Depends(get_cursor_dep)):
    first_name, last_name = str(payload.get("firstName", "")).strip(), str(payload.get("lastName", "")).strip()
    if not first_name or not last_name:
        raise HTTPException(status_code=422, detail="Nombre y apellido son obligatorios")
    cur.execute("""INSERT INTO livre_crm_vehicle_owners (first_name, last_name, identification, email, phone, city, province, street, street_number, notes, raw_payload)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb) RETURNING *""",
                (first_name, last_name, payload.get("cuitNumberTitular") or payload.get("idNumber"), payload.get("contactEmail"), payload.get("contactPhone"), payload.get("city"), payload.get("province"), payload.get("streetName"), payload.get("streetNumber"), payload.get("notes"), json.dumps(payload)))
    return {**dict(cur.fetchone()), "source": "livre-cloud"}


@router.put("/vehicle-owners/{owner_id}")
def update_live_vehicle_owner(owner_id: int, payload: dict = Body(...), user: dict = Depends(require_auth), cur=Depends(get_cursor_dep)):
    cur.execute("""UPDATE livre_crm_vehicle_owners SET first_name=%s,last_name=%s,identification=%s,email=%s,phone=%s,city=%s,province=%s,street=%s,street_number=%s,notes=%s,raw_payload=%s::jsonb,updated_at=now() WHERE id=%s RETURNING *""",
                (payload.get("firstName", "").strip(), payload.get("lastName", "").strip(), payload.get("cuitNumberTitular") or payload.get("idNumber"), payload.get("contactEmail"), payload.get("contactPhone"), payload.get("city"), payload.get("province"), payload.get("streetName"), payload.get("streetNumber"), payload.get("notes"), json.dumps(payload), owner_id))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Propietario no encontrado en Livre Cloud")
    return {**dict(row), "source": "livre-cloud"}


@router.put("/vehicles/{vehicle_id}")
def update_live_vehicle(vehicle_id: int, payload: dict = Body(...), user: dict = Depends(require_auth), cur=Depends(get_cursor_dep)):
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
    user: dict = Depends(require_auth),
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
