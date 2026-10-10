"""API autenticada de consulta para los datos locales de Livre."""
import json
import logging
import math
from datetime import date, timedelta
from decimal import Decimal

import httpx
from fastapi import APIRouter, Body, Depends, Query, HTTPException
from app.core.database import get_cursor_dep
from app.core.jwt import require_auth
from app.finance import PROVIDER_COMPLETED_STATUSES, finalize_trip_financials
from app.integrations import magiis_web
from app.integrations.magiis_crm import MagiisCrmReader
from app.crm.sync import sync_all
from app.mobility.schemas import DriverOperationalStatusResponse, DriverOperationalStatusUpdate

router = APIRouter(prefix="/crm", tags=["crm"])
log = logging.getLogger(__name__)

_RESOURCES = {
    "passengers": "magiis_crm_passengers",
    "drivers": "magiis_crm_drivers",
    "vehicles": "magiis_crm_vehicles",
    "travels": "magiis_crm_travels",
    "vehicle_owners": "magiis_crm_vehicle_owners",
}

# These accounts remain in livre_users/CRM, but are not GPS/map conductors.
# Keep this list keyed by verified production user_id, not display names:
# "Perez Ezequiel" and "IBAÑEZ PUCHULO CARLOS MARTIN" are real drivers.
_HIDDEN_GPS_DRIVER_USER_IDS = (
    "3f36895f-8d5d-48a9-8fe5-4309a503f91b",  # Ezequiel Perez
    "027390ab-b0e4-42da-a06e-d1e9b40395e5",  # Sergio Moreno
    "16142c7f-3210-463b-9a30-b4228149f3ee",  # GPS QA Production
    "b1570ce9-6930-412e-bf6b-dea572402f7a",  # Marti Ibañez
    "64c8bdb7-dbff-4704-be78-4284eb713f35",  # Livre Prueba
    "4d96ce43-d0c1-41fb-9700-181cf26ca481",  # Livre
)


def _local_page(cur, table: str, page: int, size: int, q: str = ""):
    """Lee el snapshot local importado; nunca abre una sesión externa."""
    where = ""
    params = []
    if q.strip():
        where = " WHERE raw_payload::text ILIKE %s"
        params.append("%" + q.strip() + "%")
    cur.execute(f"SELECT count(*) AS total FROM {table}{where}", params)
    total = int(cur.fetchone()["total"])
    cur.execute(
        f"SELECT magiis_id, raw_payload FROM {table}{where} ORDER BY magiis_id DESC LIMIT %s OFFSET %s",
        params + [size, (page - 1) * size],
    )
    items = [{"magiis_id": row["magiis_id"], "raw_payload": row["raw_payload"]} for row in cur.fetchall()]
    return {"source": "local", "items": items, "page": page, "size": size, "total": total}


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


def _find_provider_id(value: object, keys: tuple[str, ...]) -> int | None:
    """Busca el id real devuelto por MAGIIS, incluso si viene anidado."""
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if candidate not in (None, ""):
                try:
                    return int(candidate)
                except (TypeError, ValueError):
                    pass
        for child in value.values():
            found = _find_provider_id(child, keys)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_provider_id(child, keys)
            if found is not None:
                return found
    return None


def _provider_gross_fare(item: dict) -> Decimal | None:
    """Accept only an explicit positive provider amount; never turn missing into zero."""
    for key in ("gross_fare", "grossFare", "fare", "price", "amount"):
        value = item.get(key)
        if value in (None, ""):
            continue
        try:
            amount = Decimal(str(value).replace("$", "").replace(",", ".").strip())
        except Exception:
            log.warning("[CRM FINANCE] invalid %s=%r; leaving fare unchanged", key, value)
            continue
        if amount > 0:
            return amount
        log.warning("[CRM FINANCE] non-positive %s=%r; leaving fare unknown", key, value)
    return None


def _sync_trip_assignment(cur, item: dict) -> bool:
    """Guarda asignación de MAGIIS sin borrar datos ya recolectados."""
    travel_id = _find_provider_id(item, ("travelId",))
    if travel_id is None:
        return False
    assignment_payload = {
        key: item.get(key)
        for key in (
            "travelId",
            "state",
            "driverId",
            "driverFirstName",
            "driverLastName",
            "vehicleDomain",
            "vehicleMark",
            "vehicleModel",
            "vehicleColor",
            "carPlate",
        )
        if item.get(key) is not None
    }
    provider_gross = _provider_gross_fare(item)
    cur.execute(
        """
        UPDATE livi_trips
        SET gross_fare = COALESCE(gross_fare, %s),
            fare = COALESCE(fare, %s),
            magiis_status = COALESCE(%s, magiis_status),
            magiis_driver_id = COALESCE(%s, magiis_driver_id),
            magiis_driver_first_name = COALESCE(NULLIF(%s, ''), magiis_driver_first_name),
            magiis_driver_last_name = COALESCE(NULLIF(%s, ''), magiis_driver_last_name),
            magiis_vehicle_domain = COALESCE(NULLIF(%s, ''), magiis_vehicle_domain),
            magiis_vehicle_mark = COALESCE(NULLIF(%s, ''), magiis_vehicle_mark),
            magiis_vehicle_model = COALESCE(NULLIF(%s, ''), magiis_vehicle_model),
            magiis_vehicle_color = COALESCE(NULLIF(%s, ''), magiis_vehicle_color),
            magiis_car_plate = COALESCE(NULLIF(%s, ''), magiis_car_plate),
            tracking_driver_name = COALESCE(NULLIF(%s, ''), tracking_driver_name),
            tracking_plate = COALESCE(NULLIF(%s, ''), tracking_plate),
            tracking_vehicle_color = COALESCE(NULLIF(%s, ''), tracking_vehicle_color),
            tracking_vehicle_model = COALESCE(NULLIF(%s, ''), tracking_vehicle_model),
            magiis_response = %s,
            magiis_updated_at = NOW(),
            updated_at = NOW()
        WHERE magiis_travel_id = %s
        """,
        (
            provider_gross,
            provider_gross,
            item.get("state"),
            _find_provider_id(item, ("driverId",)),
            str(item.get("driverFirstName") or ""),
            str(item.get("driverLastName") or ""),
            str(item.get("vehicleDomain") or ""),
            str(item.get("vehicleMark") or ""),
            str(item.get("vehicleModel") or ""),
            str(item.get("vehicleColor") or ""),
            str(item.get("carPlate") or ""),
            str(item.get("driverFirstName") or ""),
            str(item.get("carPlate") or item.get("vehicleDomain") or ""),
            str(item.get("vehicleColor") or ""),
            str(item.get("vehicleModel") or ""),
            json.dumps(assignment_payload, ensure_ascii=False),
            travel_id,
        ),
    )
    updated = cur.rowcount
    if str(item.get("state") or "").strip().lower() in PROVIDER_COMPLETED_STATUSES:
        cur.execute(
            """UPDATE livi_trips
               SET status = 'completed', completed_at = COALESCE(completed_at, NOW()), updated_at = NOW()
               WHERE magiis_travel_id = %s AND status <> 'cancelled'
               RETURNING id""",
            (travel_id,),
        )
        trip = cur.fetchone()
        if trip:
            finalize_trip_financials(cur, trip["id"] if isinstance(trip, dict) else trip[0])
    return updated > 0


def _persist_created_record(cur, table: str, response: object, fallback: dict, id_keys: tuple[str, ...]) -> None:
    """Replica inmediatamente una alta exitosa en la tabla local de CRM."""
    provider_id = _find_provider_id(response, id_keys)
    if provider_id is None:
        return
    raw_payload = response if isinstance(response, (dict, list)) else fallback
    cur.execute(
        f"""INSERT INTO {table} (magiis_id, raw_payload)
            VALUES (%s, %s::jsonb)
            ON CONFLICT (magiis_id) DO UPDATE SET raw_payload = EXCLUDED.raw_payload, last_seen_at = now()""",
        (provider_id, json.dumps(raw_payload, separators=(",", ":"))),
    )

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


def _operational_snapshot(cur):
    """Returns one consistent Livre snapshot of accounts, trips and GPS."""
    cur.execute(
        """
        SELECT a.user_id, a.active, a.operational_status,
               (a.active = TRUE
                AND a.device_session_active = TRUE
                AND a.device_last_seen_at >= NOW() - INTERVAL '2 minutes') AS session_active,
               u.name, u.username,
               CASE WHEN a.active = TRUE
                          AND a.device_session_active = TRUE
                          AND a.device_last_seen_at >= NOW() - INTERVAL '2 minutes'
                    THEN a.current_latitude END AS latitude,
               CASE WHEN a.active = TRUE
                          AND a.device_session_active = TRUE
                          AND a.device_last_seen_at >= NOW() - INTERVAL '2 minutes'
                    THEN a.current_longitude END AS longitude,
               CASE WHEN a.active = TRUE
                          AND a.device_session_active = TRUE
                          AND a.device_last_seen_at >= NOW() - INTERVAL '2 minutes'
                    THEN a.location_updated_at END AS location_updated_at,
               COALESCE(driver_source.raw_payload->>'vehicleDomain', vehicle_source.raw_payload #>> '{identificationCard,domain}') AS associated_vehicle_plate,
               COALESCE(
                   NULLIF(trim(concat_ws(' ', driver_source.raw_payload->>'vehicleBrand', driver_source.raw_payload->>'vehicleModel')), ''),
                   NULLIF(trim(concat_ws(' ', vehicle_source.raw_payload #>> '{identificationCard,mark}', vehicle_source.raw_payload #>> '{identificationCard,model}')), '')
               ) AS associated_vehicle_model,
               COALESCE(vehicle_source.raw_payload #>> '{identificationCard,color}', driver_source.raw_payload->>'vehicleColor') AS associated_vehicle_color,
               active_trip.id AS trip_id,
               active_trip.status AS trip_status,
               active_trip.tracking_driver_name,
               active_trip.tracking_plate,
               active_trip.tracking_vehicle_model,
               active_trip.tracking_vehicle_color
        FROM livre_driver_accounts a
        JOIN livre_users u ON u.id = a.user_id
        LEFT JOIN magiis_crm_drivers driver_source
          ON driver_source.magiis_id = a.magiis_driver_id
        LEFT JOIN magiis_crm_vehicles vehicle_source
          ON vehicle_source.magiis_id::text = driver_source.raw_payload->>'vehicleId'
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
        WHERE a.user_id <> ALL(%s)
        ORDER BY u.name NULLS LAST, a.user_id
        """,
        (list(_HIDDEN_GPS_DRIVER_USER_IDS),),
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
        items.append({
            "driverUserId": str(row["user_id"]),
            "driverName": driver_name,
            "sessionActive": bool(row["session_active"]),
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
            "source": "livre_driver_accounts+device_session+driver_vehicle_association+livi_trips",
        })
    return items


@router.get("/operational")
def operational(user: dict = Depends(require_auth), cur=Depends(get_cursor_dep)):
    """Returns the current Livre operational snapshot."""
    drivers = _operational_snapshot(cur)
    cur.execute("SELECT count(*) AS total FROM livi_trips")
    travels = int(cur.fetchone()["total"])
    in_travel = sum(item["operationalState"] == "EN VIAJE" for item in drivers)
    available = sum(item["operationalStatus"] == "available" for item in drivers)
    unavailable = sum(item["operationalStatus"] == "unavailable" for item in drivers)
    out_of_service = sum(item["operationalStatus"] == "out_of_service" for item in drivers)
    return {
        "source": "livre",
        "status": {"travelsQuantity": travels, "workingVehiclesQuantity": len(drivers), "clientsIndividualsAmount": 0},
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
    """Listado de conductores almacenados en la base local."""
    return _local_page(cur, "magiis_crm_drivers", page, size, driver_name or driver_code or phone or email or vehicle)


@router.get("/drivers/{driver_user_id}/detail")
def live_driver_detail(driver_user_id: int, user: dict = Depends(require_auth)):
    """Detalle en vivo del chofer según la ruta observada en el Web."""
    session = magiis_web.login()
    with MagiisCrmReader(session) as reader:
        return reader.driver_detail_web(driver_user_id)

@router.get("/operational/driver/{driver_user_id}")
def operational_driver(driver_user_id: int, user: dict = Depends(require_auth)):
    session = magiis_web.login()
    with MagiisCrmReader(session) as reader:
        return reader.driver(driver_user_id)


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
    """Fuerza una sincronización: queda deshabilitada porque MAGIIS no es fuente operativa."""
    return sync_all()


@router.get("/summary")
def summary(user: dict = Depends(require_auth), cur=Depends(get_cursor_dep)):
    cur.execute(
        """
        SELECT
          (SELECT count(*) FROM magiis_crm_passengers) AS passengers,
          (SELECT count(*) FROM magiis_crm_drivers) AS drivers,
          (SELECT count(*) FROM magiis_crm_vehicles) AS vehicles,
          (SELECT count(*) FROM livi_trips) AS travels,
          (SELECT jsonb_object_agg(COALESCE(status, 'sin_estado'), total)
             FROM (SELECT status, count(*) AS total FROM livi_trips GROUP BY status) s) AS travel_states,
          (SELECT row_to_json(s) FROM (
             SELECT id, started_at, finished_at, status, resources
             FROM magiis_crm_sync_runs ORDER BY id DESC LIMIT 1
          ) s) AS last_sync
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
):
    """Comisiones de agencia del panel Web, solo lectura contra MAGIIS."""
    today = date.today().isoformat()
    # El panel envía fechas DD/MM/YYYY al reporte, no fechas ISO.
    def report_date(value: str) -> str:
        if not value:
            value = today
        try:
            return date.fromisoformat(value).strftime("%d/%m/%Y")
        except ValueError:
            return value

    session = magiis_web.login()
    with MagiisCrmReader(session) as reader:
        owners = reader.agency_commission_owners()
        report = reader.agency_commission_report(
            date_from=report_date(date_from),
            date_to=report_date(date_to),
            owner_id=owner_id,
            page=page,
            size=size,
        )
    return {
        "source": "magiis",
        "date_from": date_from or today,
        "date_to": date_to or today,
        "owner_id": owner_id,
        "owners": owners,
        **report,
    }


@router.get("/travel-history")
def travel_history(
    page: int = Query(default=1, ge=1, le=10000),
    size: int = Query(default=50, ge=1, le=100),
    q: str = Query(default="", max_length=120),
    state: str = Query(default="", max_length=120),
    date_from: str = Query(default="", max_length=10),
    date_to: str = Query(default="", max_length=10),
    user: dict = Depends(require_auth),
):
    """Histórico de viajes del reporte Web 023 de MAGIIS, solo lectura."""
    session = magiis_web.login()
    with MagiisCrmReader(session) as reader:
        # El reporte 023 trae la sumatoria que el panel muestra debajo de la tabla.
        # Se consulta una vez completo para no sumar solo la página visible.
        result = reader.travel_history_page(page=1, size=1000000, date_from=date_from, date_to=date_to, state=state, find=q)
    page_info = result.get("page", result)
    content = page_info.get("content", []) if isinstance(page_info, dict) else []
    items = []
    for entry in content:
        row = entry.get("row", entry) if isinstance(entry, dict) else entry
        if isinstance(row, list):
            row = {f"Col{index + 1}": value for index, value in enumerate(row)}
        items.append(row)
    def amount(index: int) -> float:
        total = 0.0
        for row in items:
            value = row.get(f"Col{index}")
            if isinstance(value, (int, float)):
                total += float(value)
            elif isinstance(value, str):
                try:
                    total += float(value.replace("$", "").replace("ARS", "").replace(".", "").replace(",", ".").strip())
                except ValueError:
                    pass
        return round(total, 2)
    total = len(items)
    start = (page - 1) * size
    return {
        "source": "magiis",
        "items": items[start:start + size],
        "page": page,
        "size": size,
        "total": total,
        "summary": {"trips": total, "final_amount_ars": amount(11), "amount_ars": amount(14), "budget_ars": amount(15)},
    }


@router.get("/passengers")
def live_passengers(
    page: int = Query(default=1, ge=1, le=10000),
    size: int = Query(default=20, ge=1, le=100),
    q: str = Query(default="", max_length=120),
    user: dict = Depends(require_auth),
    cur=Depends(get_cursor_dep),
):
    """Clientes almacenados en la base local."""
    return _local_page(cur, "magiis_crm_passengers", page, size, q)


@router.get("/passengers/{passenger_id}")
def live_passenger(passenger_id: int, user: dict = Depends(require_auth)):
    """Detalle de un cliente, solo lectura contra MAGIIS."""
    session = magiis_web.login()
    with MagiisCrmReader(session) as reader:
        return reader.passenger(passenger_id)


@router.put("/travels/{travel_id}/cancel")
def cancel_live_travel(
    travel_id: int,
    reason: str = Query(default="Cancelación solicitada desde Gestión de Viajes", max_length=250),
    user: dict = Depends(require_auth),
):
    """Cancela un viaje activo usando el contrato Web observado en MAGIIS."""
    session = magiis_web.login()
    response, _, _ = magiis_web.cancel_travel(
        session=session,
        travel_id=travel_id,
        operator_name=user.get("name") or "Livre",
        reason=reason,
        canceled_by="CARRIER_MANAGER",
    )
    if response.status_code >= 400:
        raise HTTPException(status_code=502, detail="MAGIIS no pudo cancelar el viaje")
    return {"travel_id": travel_id, "status": "cancelled", "message": "Viaje cancelado correctamente."}


@router.get("/vehicles/live")
def live_vehicles(
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    q: str = Query(default="", max_length=120),
    user: dict = Depends(require_auth),
    cur=Depends(get_cursor_dep),
):
    """Vehículos almacenados en la base local."""
    return _local_page(cur, "magiis_crm_vehicles", page, size, q)


@router.get("/vehicles/types")
def live_vehicle_types(user: dict = Depends(require_auth)):
    session = magiis_web.login()
    with MagiisCrmReader(session) as reader:
        return {"items": reader.vehicle_types()}


@router.post("/vehicles")
def create_live_vehicle(payload: dict = Body(...), user: dict = Depends(require_auth), cur=Depends(get_cursor_dep)):
    # La identidad del carrier queda en backend; el navegador no decide el carrier.
    provider_payload = dict(payload)
    provider_payload["carrierUserId"] = str(magiis_web.CARRIER_USER_ID)
    session = magiis_web.login()
    with MagiisCrmReader(session) as reader:
        response = reader.create_vehicle(provider_payload)
    _persist_created_record(cur, "magiis_crm_vehicles", response, provider_payload, ("vehicleId", "vehicle_id", "id"))
    return response


@router.post("/drivers")
def create_live_driver(payload: dict = Body(...), user: dict = Depends(require_auth), cur=Depends(get_cursor_dep)):
    # El carrier se fija en backend para no confiar en datos enviados por el navegador.
    provider_payload = dict(payload)
    provider_payload["carrierUserId"] = int(magiis_web.CARRIER_USER_ID)
    session = magiis_web.login()
    with MagiisCrmReader(session) as reader:
        response = reader.create_driver(provider_payload)
    _persist_created_record(cur, "magiis_crm_drivers", response, provider_payload, ("driverUserId", "driverId", "driver_id", "id"))
    return response


@router.post("/clients")
def create_live_client(payload: dict = Body(...), user: dict = Depends(require_auth), cur=Depends(get_cursor_dep)):
    provider_payload = dict(payload)
    session = magiis_web.login()
    with MagiisCrmReader(session) as reader:
        response = reader.create_passenger(provider_payload)
    _persist_created_record(cur, "magiis_crm_passengers", response, provider_payload, ("passengerUserId", "id", "passengerId"))
    return response


@router.get("/vehicle-owners")
def live_vehicle_owners(page: int = Query(default=1, ge=1), size: int = Query(default=50, ge=1, le=100), q: str = Query(default="", max_length=120), user: dict = Depends(require_auth), cur=Depends(get_cursor_dep)):
    result = _local_page(cur, "magiis_crm_vehicle_owners", page, size, q)
    return {key: result[key] for key in ("items", "total", "page", "size")}


@router.post("/confirm-email")
def confirm_crm_email(payload: dict = Body(...), user: dict = Depends(require_auth)):
    email = str(payload.get("email", "")).strip()
    role = str(payload.get("role", "")).strip().upper()
    if not email or role not in {"DRIVER", "VEHICLEOWNER"}:
        from fastapi import HTTPException
        raise HTTPException(status_code=422, detail="Email y rol son obligatorios")
    session = magiis_web.login()
    with MagiisCrmReader(session) as reader:
        return reader.confirm_email(email=email, role=role)


@router.get("/vehicle-owners/{owner_id}")
def live_vehicle_owner(owner_id: int, user: dict = Depends(require_auth)):
    session = magiis_web.login()
    with MagiisCrmReader(session) as reader:
        return reader.vehicle_owner(owner_id)


@router.post("/vehicle-owners")
def create_live_vehicle_owner(payload: dict = Body(...), user: dict = Depends(require_auth), cur=Depends(get_cursor_dep)):
    provider_payload = dict(payload)
    provider_payload["carrierId"] = str(magiis_web.CARRIER_USER_ID)
    session = magiis_web.login()
    with MagiisCrmReader(session) as reader:
        response = reader.create_vehicle_owner(provider_payload)
    _persist_created_record(cur, "magiis_crm_vehicle_owners", response, provider_payload, ("vehicleOwnerId", "id"))
    return response


@router.put("/vehicle-owners/{owner_id}")
def update_live_vehicle_owner(owner_id: int, payload: dict = Body(...), user: dict = Depends(require_auth)):
    session = magiis_web.login()
    with MagiisCrmReader(session) as reader:
        return reader.update_vehicle_owner(owner_id, payload)


@router.put("/vehicles/{vehicle_id}")
def update_live_vehicle(vehicle_id: int, payload: dict = Body(...), user: dict = Depends(require_auth)):
    session = magiis_web.login()
    with MagiisCrmReader(session) as reader:
        return reader.update_vehicle(vehicle_id, payload)


@router.get("/driver-shift-closures")
def driver_shift_closures(
    driver_user_id: str = Query(default="", max_length=120),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    user: dict = Depends(require_auth),
    cur=Depends(get_cursor_dep),
):
    """Consulta CRM de cierres persistidos en Livre, sin leer MAGIIS."""
    where = "WHERE c.driver_user_id = %s" if driver_user_id.strip() else ""
    params = [driver_user_id.strip()] if driver_user_id.strip() else []
    cur.execute(
        f"""SELECT c.id, c.driver_user_id, u.name AS driver_name, c.closure_date,
                   c.closed_at, c.trip_count, c.total_revenue,
                   c.vehicle_id, c.vehicle_label, c.vehicle_model, c.plate, c.owner_name,
                   (SELECT count(*) FROM livre_driver_shift_closure_trips ct
                    WHERE ct.closure_id = c.id) AS traced_trip_count
            FROM livre_driver_shift_closures c
            JOIN livre_users u ON u.id = c.driver_user_id
            {where}
            ORDER BY c.closure_date DESC, c.closed_at DESC
            LIMIT %s OFFSET %s""",
        params + [limit, offset],
    )
    return [dict(row) for row in cur.fetchall()]


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
    table = _RESOURCES.get(resource)
    if not table:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Recurso CRM inexistente")
    where_parts = []
    params = []
    if q.strip():
        where_parts.append("raw_payload::text ILIKE %s")
        params.append("%" + q.strip() + "%")
    if resource == "travels" and state.strip():
        where_parts.append("state = %s")
        params.append(state.strip())
    if resource == "travels" and travel_date.strip():
        where_parts.append("travel_date::date = %s::date")
        params.append(travel_date.strip())
    where = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""
    cur.execute(f"SELECT count(*) AS total FROM {table}{where}", params)
    total = int(cur.fetchone()["total"])
    columns = "magiis_id, raw_payload, first_seen_at, last_seen_at"
    if resource == "travels":
        columns = "magiis_id, raw_payload, travel_date, state, passenger_magiis_id, driver_magiis_id, first_seen_at, last_seen_at"
    cur.execute(
        f"""SELECT {columns}
            FROM {table}{where}
            ORDER BY magiis_id DESC LIMIT %s OFFSET %s""",
        params + [limit, offset],
    )
    rows = [dict(row) for row in cur.fetchall()]
    return {"resource": resource, "total": total, "limit": limit, "offset": offset, "items": rows}
