"""API autenticada de consulta para los datos recolectados de MAGIIS."""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query, HTTPException
from app.core.database import get_cursor_dep
from app.core.jwt import require_auth
from app.integrations import magiis_web
from app.integrations.magiis_crm import MagiisCrmReader

router = APIRouter(prefix="/crm", tags=["crm"])

_RESOURCES = {
    "passengers": "magiis_crm_passengers",
    "drivers": "magiis_crm_drivers",
    "vehicles": "magiis_crm_vehicles",
    "travels": "magiis_crm_travels",
}


@router.get("/operational")
def operational(user: dict = Depends(require_auth)):
    """Contrato server-side del panel Control operacional de MAGIIS Web."""
    session = magiis_web.login()
    with MagiisCrmReader(session) as reader:
        return {
            "status": reader.operational_status(),
            "driver_totals": reader.driver_totals(),
            "drivers": reader.driver_dashboard(vehicle_id_switch=False),
            "drivers_by_vehicle": reader.driver_dashboard(vehicle_id_switch=True),
            "report_042": reader.operational_report(),
            "chats": reader.chats_all(),
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
):
    """Listado en vivo de choferes, replicando el reporte Web 082."""
    session = magiis_web.login()
    with MagiisCrmReader(session) as reader:
        return reader.driver_report_page(
            page=page,
            size=size,
            driver_code=driver_code,
            driver_name=driver_name,
            phone=phone,
            email=email,
            vehicle=vehicle,
            vehicle_type=vehicle_type,
            operability=operability,
        )


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


@router.get("/summary")
def summary(user: dict = Depends(require_auth), cur=Depends(get_cursor_dep)):
    cur.execute(
        """
        SELECT
          (SELECT count(*) FROM magiis_crm_passengers) AS passengers,
          (SELECT count(*) FROM magiis_crm_drivers) AS drivers,
          (SELECT count(*) FROM magiis_crm_vehicles) AS vehicles,
          (SELECT count(*) FROM magiis_crm_travels) AS travels,
          (SELECT jsonb_object_agg(COALESCE(state, 'sin_estado'), total)
             FROM (SELECT state, count(*) AS total FROM magiis_crm_travels GROUP BY state) s) AS travel_states,
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
):
    """Consulta en vivo de gestión de viajes, solo lectura contra MAGIIS."""
    today = date.today()
    travel_date_from = date_from or today.isoformat()
    travel_date_to = date_to or (today + timedelta(days=31)).isoformat()
    session = magiis_web.login()
    with MagiisCrmReader(session) as reader:
        page_data = reader.travel_page(
            page=page,
            size=size,
            travel_date_from=travel_date_from,
            travel_date_to=travel_date_to,
            state=state,
            programmed=programmed,
            find=q,
            is_pax_vip=vip,
        )
        totals = reader.travel_totals(
            travel_date_from=travel_date_from,
            travel_date_to=travel_date_to,
            find=q,
            programmed=programmed,
            is_pax_vip=vip,
        )
    page_info = page_data.get("page", page_data)
    return {
        "source": "magiis",
        "items": page_info.get("content", []),
        "page": page_info.get("number", page - 1) + 1,
        "size": page_info.get("size", size),
        "total": page_info.get("totalElements", 0),
        "totals": totals,
        "date_from": travel_date_from,
        "date_to": travel_date_to,
    }


@router.get("/passengers")
def live_passengers(
    page: int = Query(default=1, ge=1, le=10000),
    size: int = Query(default=20, ge=1, le=100),
    q: str = Query(default="", max_length=120),
    user: dict = Depends(require_auth),
):
    """Consulta en vivo de clientes, solo lectura contra MAGIIS."""
    session = magiis_web.login()
    with MagiisCrmReader(session) as reader:
        page_data = reader.passenger_page(page=page, size=size, find=q.strip())
        totals = reader.client_totals()
    page_info = page_data.get("page", page_data)
    return {
        "source": "magiis",
        "items": page_info.get("content", []),
        "page": page_info.get("number", page - 1) + 1,
        "size": page_info.get("size", size),
        "total": page_info.get("totalElements", 0),
        "totals": totals,
    }


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
