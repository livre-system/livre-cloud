"""API autenticada de consulta para los datos recolectados de MAGIIS."""
from fastapi import APIRouter, Depends, Query
from app.core.database import get_cursor_dep
from app.core.jwt import require_auth

router = APIRouter(prefix="/crm", tags=["crm"])

_RESOURCES = {
    "passengers": "magiis_crm_passengers",
    "drivers": "magiis_crm_drivers",
    "vehicles": "magiis_crm_vehicles",
    "travels": "magiis_crm_travels",
}


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
